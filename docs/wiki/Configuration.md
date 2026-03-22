# Configuration

tokencostscope works automatically with no configuration. This page documents manual overrides and tunable parameters.

---

## Manual Invocation

Invoke explicitly at any time:

```
/tokencostscope
```

With overrides:

```
/tokencostscope size=L files=12 complexity=high
/tokencostscope steps=implement,test,qa
/tokencostscope review_cycles=3
/tokencostscope review_cycles=0
```

---

## Override Reference

| Override | Effect |
|----------|--------|
| `size=XS\|S\|M\|L` | Set size class explicitly (overrides step-count inference) |
| `files=N` | Set file count (used for per-file activity counts: reads, edits, test writes) |
| `complexity=low\|medium\|high` | Set complexity multiplier (0.7×, 1.0×, 1.5×) |
| `steps=a,b,c` | Estimate only the listed pipeline steps |
| `project_type=greenfield\|refactor\|bug_fix\|migration\|docs` | Set project type |
| `language=python\|typescript\|go\|...` | Set primary language |
| `review_cycles=N` | Override PR review cycle count. Use `0` to suppress the PR Review Loop row entirely. |
| `avg_file_lines=N` | Map N to size bracket: ≤49 → small (3k tokens/read), 50–500 → medium (10k), ≥501 → large (20k). Applies to files not measured on disk. |

---

## Parallel Agent Accounting

Parallel steps are detected automatically from plan text. No manual override is available — detection is fully automatic.

**Detected patterns (case-insensitive):**
- `"in parallel"`, `"simultaneously"`, `"concurrently"`
- `"parallel:"` prefix followed by step names
- `"∥"`, `"[parallel]"`, `"(parallel)"`

**Example plan text that triggers detection:**
```
Research Agent and PM Agent run in parallel, then Architect Agent sequentially.
```

**What gets discounted:**
- `input_accum × 0.75` — parallel agents start with no inherited context
- `cache_rate − 0.15` — parallel agents miss the warmed cache prefix

**Tunable parameters** (in `references/heuristics.md`):

| Parameter | Default | Effect |
|-----------|---------|--------|
| `parallel_input_discount` | 0.75 | Input accumulation multiplier for parallel steps |
| `parallel_cache_rate_reduction` | 0.15 | Cache rate reduction for parallel steps |
| `parallel_cache_rate_floor` | 0.05 | Minimum effective cache hit rate |

---

## Time-Based Decay

As your calibration history grows, older records exert less influence on current estimates via exponential time-decay weighting. Older sessions are never deleted — the skill preserves your full history.

**Tunable parameters** (in `references/heuristics.md`):

| Parameter | Default | Effect |
|-----------|---------|--------|
| `decay_halflife_days` | 30 | Halflife for exponential decay: 30 days old → 50% influence |

**Cold-start guard:** Records are NOT decayed when fewer than 5 records are available in a calibration stratum (size-class, step, or signature). This prevents pathological early down-weighting.

---

## Per-Signature Calibration

After 3+ runs of the same pipeline signature (e.g., the same ordered sequence of steps), a per-signature calibration factor activates. This captures cost profiles unique to your workflow without requiring manual tuning.

**Tunable parameters** (in `references/heuristics.md`):

| Parameter | Default | Effect |
|-----------|---------|--------|
| `per_signature_min_samples` | 3 | Minimum runs required for per-signature factor activation |

---

## Mid-Session Cost Tracking

During your session, tokencostscope periodically checks spend against the pessimistic estimate and warns if you're approaching the upper band. Warnings are sampled to avoid spam.

**Tunable parameters** (in `references/heuristics.md`):

| Parameter | Default | Effect |
|-----------|---------|--------|
| `midcheck_warn_threshold` | 0.80 | Warn when actual spend reaches 80% of pessimistic estimate |
| `midcheck_sampling_bytes` | 50000 | Check interval (approx 50KB) to avoid verbosity |
| `midcheck_cooldown_bytes` | 200000 | Once warned, cooldown before checking again (~200KB) |

---

## File Size Awareness

tokencostscope auto-measures file sizes when paths are present in the plan:

1. **Auto-stat (Layer 1):** Runs `wc -l` on extractable file paths. Files existing on disk
   are assigned a size bracket based on line count. Cap: 30 files per estimate.
2. **Override (Layer 2):** `avg_file_lines=N` sets the bracket for all unmeasured files
   (new files, missing files, unextracted paths). Use for greenfield projects.
3. **Default (Layer 3):** Medium bracket (10,000 tokens/read) — identical to v1.4.0 behavior.

**Bracket definitions:**

| Bracket | Line Count | File Read Input | File Edit Input |
|---------|-----------|-----------------|-----------------|
| Small   | ≤ 49      | 3,000           | 1,000           |
| Medium  | 50–500    | 10,000          | 2,500           |
| Large   | ≥ 501     | 20,000          | 5,000           |

**Example:** A plan with 5 files (3 existing, 2 new) with `avg_file_lines=600`:
- Existing files: measured on disk → assigned brackets from line counts
- New files: `avg_file_lines=600` → large bracket (20,000 tokens/read)

**Tunable parameters** (in `references/heuristics.md`):

| Parameter | Default | Effect |
|-----------|---------|--------|
| `file_size_small_max` | 49 | Lines ≤ this value → small bracket |
| `file_size_large_min` | 501 | Lines ≥ this value → large bracket |
| `file_measurement_cap` | 30 | Max files measured per estimate |

---

## Confidence Bands

| Band | Cache Hit | Multiplier |
|------|-----------|------------|
| Optimistic | 60% | 0.6× |
| Expected | 50% | 1.0× |
| Pessimistic | 30% | 3.0× |

For parallel steps, cache hit rates are reduced by `parallel_cache_rate_reduction` (default 0.15), floored at `parallel_cache_rate_floor` (default 0.05).

---

## PR Review Loop

The PR Review Loop row models the iterative review-fix-re-review cycle. It uses a geometric decay formula:

```
cost(N) = C × (1 − 0.6^N) / 0.4
```

Where `C = staff_review_expected + engineer_final_plan_expected` and `N` is the cycle count per band (Optimistic=1, Expected=default 2, Pessimistic=default×2).

Default `review_cycles = 2` is set in `references/heuristics.md` and can be overridden per-invocation with `review_cycles=N`.

---

## Pipeline Step Reference

Default pipeline steps and their assigned models:

| Step | Model | Notes |
|------|-------|-------|
| Research Agent | Sonnet | |
| Architect Agent | Opus | |
| Engineer Initial Plan | Sonnet | |
| Staff Review | Opus | |
| Engineer Final Plan | Sonnet | |
| Test Writing | Sonnet | |
| Implementation | Sonnet | Opus for L-size |
| QA | Haiku | |
| PR Review Loop | Opus+Sonnet | Composite: Staff Review + Engineer Final Plan per cycle |

Map your own pipeline step names to the closest defaults — the formulas are pipeline-agnostic.
