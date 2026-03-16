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

---

## Parallel Agent Accounting

Parallel steps are detected automatically from plan text. No override needed in most cases.

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
