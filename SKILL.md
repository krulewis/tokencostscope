---
name: tokencostscope
version: 2.0.0
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
- The user invokes `/tokencostscope status` → run the **Status Dashboard** mode (see below)

Do NOT activate when:
- No plan exists in the conversation yet
- The conversation is mid-implementation (code is being written, not planned)
- An estimate was already produced for the current plan in this session
- The conversation is about tokencostscope itself (avoid recursive triggering)

## Step 0 — Infer Inputs from Context

If invoked without explicit parameters, infer from the plan in conversation:

1. **Size:** Count pipeline steps mentioned → XS (1-2 steps), S (2-3), M (5-8), L (8+)
2. **Files:** Count file paths or "N files" mentions in the plan → produces `files = N`.
   Then perform file size resolution:
   a. **Extract file paths:** Collect tokens from the plan text that match ALL of these:
      (i) contain `/` or `.` followed by a known source extension (`.py`, `.ts`, `.tsx`,
      `.js`, `.jsx`, `.go`, `.rs`, `.rb`, `.java`, `.sh`, `.md`, `.json`, `.yaml`, `.yml`,
      `.toml`, `.cfg`, `.ini`, `.sql`, `.html`, `.css`, `.scss`),
      (ii) do NOT contain `://` and do not start with `http` or `https` (exclude URLs),
      (iii) are not standalone version strings (token entirely matches `v\d+\.\d+(\.\d+)*` with no path separator) (exclude bare tokens like `v1.5.0`, but keep `vendor/v2.0/config.yaml` since it has a path separator),
      (iv) contain at least one `/` or `\` path separator or a file extension (exclude bare module names that have neither).
      Deduplicate paths. Exclude known binary extensions from measurement (see heuristics.md File Size Brackets).
   b. **Classify new vs. existing:** A file is "new" ONLY if (a) the surrounding sentence/bullet
      contains "create", "new file", or "write", AND (b) the file does not exist on disk
      (wc -l reports an error for it). Files that exist on disk are always "existing" regardless
      of plan language. This prevents false positives (e.g., "write a test for foo.py" does not
      mark an existing foo.py as new).
   c. **Measure existing files (batched):** For all `new=false` paths, resolve relative to the
      working directory. Cap at `file_measurement_cap` files (default 30; first 30 by plan order).
      Run the batched command — each path must be individually double-quoted:
      ```
      wc -l -- "path1" "path2" ... 2>/dev/null || true
      ```
      (Quoting is critical for macOS paths with spaces, e.g., `/Volumes/Macintosh HD2/...`.
      Files that cannot be read due to permissions are also treated as unmeasurable.)
      Parse output line counts. Files not found produce no output → treated as unmeasurable.
      Files 31+ (beyond the cap) receive the weighted-average bracket of the first 30 measured
      files. If no files are successfully measured, overflow and cap-exceeded files use the
      override bracket or medium default.
   d. **Assign brackets:** Map each measured file's line count to a bracket per heuristics.md
      File Size Brackets table (small: ≤ 49, medium: 50–500, large: ≥ 501). New files and
      unmeasured files receive the bracket from `avg_file_lines=` override (if provided) or
      medium default.
   e. **Compute weighted average:** For fixed-count steps, compute:
      ```
      avg_file_read_tokens = (small×3,000 + medium×10,000 + large×20,000) / total_measured
      avg_file_edit_tokens = (small×1,000 + medium×2,500 + large×5,000)   / total_measured
      ```
      If total_measured = 0: avg_file_read_tokens = 10,000, avg_file_edit_tokens = 2,500.
   f. **Produce outputs:**
      - `files` — total file count (unchanged)
      - `files_measured` — count of files with disk-measured sizes
      - `files_defaulted` — count using override or default bracket
      - `file_brackets` — dict: `{"small": N, "medium": N, "large": N}` (counts per bracket)
        When no paths are extracted: `file_brackets = null` (not `{}`)
        When paths were extracted but none measurable (all binary/missing): `{"small": 0, "medium": 0, "large": 0}`
      - `avg_file_read_tokens` — weighted average read token budget (or 10,000 if no measurement)
      - `avg_file_edit_tokens` — weighted average edit token budget (or 2,500 if no measurement)
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

9. **avg_file_lines override:** If `avg_file_lines=N` is provided as an explicit parameter,
   map N to a size bracket per heuristics.md boundaries: N ≤ 49 → small, 50 ≤ N ≤ 500 → medium,
   N ≥ 501 → large. The resulting bracket's token budgets apply to all files NOT measured on disk
   in Step 0 item 2d (new files, missing files, unextracted paths). When both `avg_file_lines=`
   and auto-measured files are present, auto-measured files use their measured bracket; only
   unmeasured files use the override bracket.

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
For **file read** and **file edit** activities, `activity_input_tokens` is bracket-dependent
when `file_brackets` is available from Step 0:

**N-scaling steps** (Implementation: N reads + N edits; Test Writing: N test writes):
```
file_read_contribution  = file_brackets["small"] × 3,000
                        + file_brackets["medium"] × 10,000
                        + file_brackets["large"] × 20,000
file_edit_contribution  = file_brackets["small"] × 1,000
                        + file_brackets["medium"] × 2,500
                        + file_brackets["large"] × 5,000
```
(Use `file_read_contribution` in place of `file_read_input_tokens × N`; similarly for edits.)

**Fixed-count steps** (Research Agent: 6 reads, Engineer Initial Plan: 4, Engineer Final Plan: 2, QA: 2):
```
file_read_contribution = avg_file_read_tokens × fixed_count
```
Where `avg_file_read_tokens` is the weighted average from Step 0 item 2e.
(Fixed-count steps perform reads only — no file edits — so `file_edit_contribution` does not apply.)

**When `file_brackets` is NOT available** (no paths extracted, no override):
All files use medium bracket defaults: `file_read_input_tokens = 10,000`,
`file_edit_input_tokens = 2,500`. Computation is identical to v1.4.0.

Output tokens for file reads (200) and file edits (1,500) are unchanged across all brackets.
Activity count for test writes = N (the `files` parameter).
All other activities (file write new, code review pass, etc.) use unchanged fixed budgets.

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

Read `step_factors` and `signature_factors` from `calibration/factors.json` if it
exists (default: {} for both).
Read size-class and global factors as before.

Derive the pipeline_signature for the current estimate inline from the `steps` array
(the same array written to `active-estimate.json`):
```
pipeline_signature = '+'.join(sorted(s.lower().replace(' ', '_') for s in steps))
```
This is the same formula used by `tokencostscope-learn.sh` line 38 to produce the
`pipeline_signature` field in history records. Note: `pipeline_signature` is NOT
stored in `active-estimate.json` (learn.sh recomputes it). Compute it inline here.

For each step, determine the factor and its source using this precedence chain:
  1. Per-step: if `step_factors[step_name]` exists and `step_factors[step_name]["status"] == "active"` → use
     `step_factors[step_name]["factor"]`, source = "S"
  2. Per-signature: if `signature_factors[pipeline_signature]` exists and its "status" == "active" → use
     `signature_factors[pipeline_signature]["factor"]`, source = "P"
  3. Size-class: if `factors[size]` exists and `factors["{size}_n"]` (e.g.,
     `factors["M_n"]`) >= 3 → use `factors[size]`, source = "Z"
  4. Global: if `factors["global"]` exists and `factors["status"] == "active"` → use
     `factors["global"]`, source = "G"
  5. No calibration: factor = 1.0, source = "--"

Step factor precedence rules — edge cases:
- If a step has both a per-step factor (active) and a signature factor, the per-step
  factor wins. Cal column shows "S:x.xx".
- If a step has a per-signature factor but the signature has status "collecting"
  (n < per_signature_min_samples=3), it is NOT applied; fall through to size-class
  or global. Cal shows "Z:x.xx", "G:x.xx", or "--" per the remaining chain.
- If a step's per-step factor has status "collecting" (n < per_step_min_samples=3),
  it is NOT applied; fall through to per-signature, size-class, or global.
- The PR Review Loop row uses the PR Review Loop's own calibration path (Step 3.5).
  Its Cal column always shows "--"; it is not subject to per-step or per-signature lookup.

Apply calibration:
```
calibrated_expected    = expected_cost × factor
calibrated_optimistic  = calibrated_expected × 0.6
calibrated_pessimistic = calibrated_expected × 3.0
```
Record the factor source per step for use in the output Cal column.

Note: Factors REPLACE (do not stack with) lower-precedence factors.

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
  "file_brackets": {"small": 0, "medium": N, "large": 0},  // null when no paths were extracted
  "files_measured": 0,
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
**File Brackets:** (if file_brackets is not null: "{files_measured} measured ({file_brackets["small"]} small, {file_brackets["medium"]} medium, {file_brackets["large"]} large); {files_defaulted} defaulted")
               (if file_brackets is null: "none (no paths extracted)")

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
## costscope estimate (v2.0.0)

**Change:** size={size}, files={N}, complexity={complexity}, type={project_type}, lang={language}
**Files:** {files} total ({files_measured} measured: {small_count} small, {medium_count} medium, {large_count} large; {files_defaulted} defaulted to {override_bracket or "medium"})
  (When no measurement occurred: **Files:** {files} total (all defaulted to medium — no paths extracted))
  (When override only, no measurement: **Files:** {files} total (all defaulted to {bracket} — avg_file_lines={N} override))
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
            (h) "size-class M=1.18x (7 runs) | global 1.12x (10 runs)"
            (i) "sig: arch+eng+impl=1.15x (4 runs)" (per-signature factor active, no per-step or size-class)
            (j) "3 steps with per-step factors | sig: arch+eng+impl=1.15x (4 runs)"
  Signature segment: when any signature_factors entry has status "active", append
  "sig: {signature}={factor}x ({n} runs)" for each active entry.
  If no signature factors are active, omit the sig segment entirely.}
{WARNING line if pricing stale}

| Step                  | Model       | Cal    | Optimistic | Expected | Pessimistic |
|-----------------------|-------------|--------|------------|----------|-------------|
| ┌ Parallel Group 1 ∥  |             |        |            |          |             |
| │ Research Agent      | Sonnet      | S:0.82 | $X.XX      | $X.XX    | $X.XX       |
| └ Architect Agent     | Opus        | G:1.12 | $X.XX      | $X.XX    | $X.XX       |
| [sequential steps]    | ...         | --     | ...        | ...      | ...         |
| PR Review Loop        | Opus+Sonnet | --     | $X.XX      | $X.XX    | $X.XX       |
| **TOTAL**             |             |        | **$X.XX**  | **$X.XX**| **$X.XX**   |
Cal: S=per-step  P=per-signature  Z=size-class  G=global  --=uncalibrated

**Cal column values:** S:x.xx = per-step factor applied · P:x.xx = per-signature factor (new in v1.6.0) · Z:x.xx = size-class factor · G:x.xx = global factor · -- = no calibration active (factor=1.0) or PR Review Loop row
**Cal column edge cases:**
- Step has active per-step AND signature/size-class/global factor → per-step wins, show "S:x.xx"
- Step has active per-signature factor (status "active") → show "P:x.xx" (only when no per-step factor)
- Step has per-signature factor with status "collecting" (n < 3) → not applied; fall through to size-class or global
- Step's per-step factor has status "collecting" (n < 3) → not applied; fall through to per-signature → size-class → global
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

## Status Dashboard Mode (`/tokencostscope status`)

When the user invokes `/tokencostscope status`, run the status analysis script and render
the results as a human-readable dashboard. This mode does **not** produce a cost estimate —
it analyzes historical calibration data and reports on accuracy health.

**Invocation:**
```bash
/usr/bin/python3 scripts/tokencostscope-status.py \
    [--history calibration/history.jsonl] \
    [--factors calibration/factors.json] \
    [--heuristics references/heuristics.md] \
    [--window SPEC] [--verbose] [--json]
```

**Window spec:** `"30d"` (last 30 days), `"10"` (last 10 sessions), `"all"` (all records),
or omit for adaptive (last 30 days OR last 10 sessions, whichever is larger).

**Output sections:**
1. **Health** — calibration status (collecting/active), active factor level, session count
2. **Accuracy** — mean/median ratio, trend (improving/stable/degrading), band hit rates
3. **Cost Attribution** — per-step actual cost totals (requires v1.7 sidecar data)
4. **Outliers** — sessions with ratio < 0.2 or > 3.0, with pattern detection
5. **Recommendations** — actionable suggestions (review cycle default, band width, outlier rate, step dominance)

**Sparse data:** If fewer than 3 clean (non-outlier, non-excluded) sessions exist, the script
outputs a "not enough data yet" message with the clean session count. Use `--verbose` to
bypass this gate and see raw data regardless.

**JSON output:** Pass `--json` to get machine-readable output with `schema_version: 1`.

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
| `avg_file_lines=N` | Map N to size bracket: ≤49 → small (3k tokens/read), 50–500 → medium (10k), ≥501 → large (20k). Applies to files not measured on disk. |

## Limitations

- Pipeline step names reflect a default workflow. Map your own steps to the closest defaults; the formulas are pipeline-agnostic.
- File size brackets use line count as a proxy for token count; actual token density varies by language and coding style. Bracket values (3k/10k/20k read; 1k/2.5k/5k edit) are heuristic averages. Auto-measurement applies when file paths are extractable and files exist on disk. Use `avg_file_lines=` when auto-measurement is not feasible (e.g., greenfield projects).
- Parallel agent modeling uses fixed discount factors; actual cache and context behavior varies by agent topology.
- Calibration requires 3+ completed sessions before corrections activate.
- Pricing data may be stale; check `last_updated` in references/pricing.md.
