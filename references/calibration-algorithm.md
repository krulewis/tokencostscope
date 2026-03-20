# Calibration Algorithm Reference

## Overview

tokencostscope learns from actual session costs to improve future estimates.
The learning loop is fully automatic: estimates are recorded during planning,
actuals are captured at session end via the Stop hook, and correction factors
are recomputed after each session.

## Data Flow

```
Plan created → SKILL.md auto-triggers → estimate produced
                                       → active-estimate.json written

Session ends → tokencostscope-learn.sh fires (Stop hook)
             → sum-session-tokens.py reads JSONL log
             → actual cost computed
             → record appended to history.jsonl
             → update-factors.py recomputes factors.json

Next session → SKILL.md reads factors.json
             → correction factor applied to Expected band
```

## Correction Factor Application

The learned factor applies to the **Expected band only**. Optimistic and Pessimistic
are recomputed as fixed ratios of the calibrated Expected:

```
calibrated_expected    = raw_expected × correction_factor
calibrated_optimistic  = calibrated_expected × 0.6
calibrated_pessimistic = calibrated_expected × 3.0
```

This preserves the semantic meaning of bands (best/typical/worst case) while
shifting the center point based on learned data.

## Factor Computation

### Phase 1: Collecting (0-2 samples)
No correction applied. Factor = 1.0.

### Phase 2: Global (3-10 samples)
A single global correction factor computed as the **trimmed mean** (trim_fraction=0.1)
of all actual/expected ratios. Trimmed mean converges faster than median for small
samples while still resisting outliers.

```
factor = trimmed_mean(actual_cost / expected_cost for all clean records, trim_fraction=0.1)
```

For N<10, k=0 (equivalent to plain mean). For N=10, k=1 (drops one extreme at each end).

### Phase 3: Stratified (10+ samples globally, 3+ per stratum)
Per-size-class factors computed independently. Each stratum (XS, S, M, L)
that has 3+ samples gets its own factor. Strata below 3 samples fall back
to the global factor.

For strata with 10+ samples, switches from trimmed mean to EWMA (alpha=0.15)
for recency weighting:

```
ewma[0] = ratios[0]
ewma[i] = 0.15 × ratios[i] + 0.85 × ewma[i-1]
factor  = ewma[last]
```

### Phase 4: Per-Step Factors (3+ samples for a given step)
Per-step factors accumulate independently for each canonical pipeline step name.
A step contributes one sample per history record that contains a non-empty
step_ratios entry for that step name.

Minimum samples before activation: 3 (per_step_min_samples in heuristics.md).
Same trimmed_mean / EWMA algorithm as size-class factors.

**Proportional attribution:** Because session JSONL does not tag turns with pipeline
step names, per-step actual cost cannot be measured directly. Instead, each step in
a session receives the session-level ratio (actual/expected). This means all steps
in the same session share the same ratio value. Differentiated per-step signal
emerges over time: steps that appear predominantly in over-estimated sessions
accumulate lower factors, and vice versa.

This cross-contamination is a known limitation. Per-step factors will not converge
to truly isolated per-step accuracy until step-level JSONL tagging is available.

**Factor precedence (Step 3e):**
  1. Per-step factor (status "active") — most specific; overrides size-class and global
  2. Size-class factor (`factors["{size}_n"] >= 3` samples in size stratum)
  3. Global factor (status "active")
  4. No calibration — factor = 1.0

**PR Review Loop exclusion:** The PR Review Loop is a composite row with its own
calibration path (per-band independent factors in Step 3.5). It is stored in
step_costs for output completeness but excluded from per-step factor computation.
Exclusion is by exact string match on the key "PR Review Loop" (case-sensitive).

**Step name stability:** Step names in step_factors are matched by exact string equality.
Renaming a step in heuristics.md resets its calibration history — the old step name's
accumulated data becomes orphaned (still present in history records but no longer
matched). Consider this cost before renaming any canonical pipeline step name.

## Actual Cost Computation

Actual cost is computed from session JSONL logs. Each assistant message
contains a `usage` object with four token fields:

```
cost = (input_tokens       × price_input
      + cache_read_tokens  × price_cache_read
      + cache_write_tokens × price_cache_write
      + output_tokens      × price_output) / 1,000,000
```

### Filtering Rules
- Only messages with `type: "assistant"` are counted
- Messages with `model: "<synthetic>"` are excluded
- Model names are normalized (date suffixes stripped)
- Unknown models fall back to Sonnet pricing

### Baseline Subtraction
The active-estimate.json records the session's cost at estimate time.
This baseline is subtracted from the total session cost to isolate
the task's cost (tokens spent before the estimate are not the task's cost).

## History Record Format (history.jsonl)

```json
{
  "timestamp": "2026-03-03T14:22:00Z",
  "size": "M",
  "files": 5,
  "complexity": "medium",
  "expected_cost": 7.01,
  "actual_cost": 8.34,
  "ratio": 1.19,
  "turn_count": 48,
  "steps": ["Research Agent", "Architect Agent", "Engineer Agent"],
  "pipeline_signature": "architect_agent+engineer_agent+research_agent",
  "project_type": "refactor",
  "language": "python",
  "step_count": 3,
  "review_cycles_estimated": 2,
  "review_cycles_actual": null
}
```

Fields `steps`, `pipeline_signature`, `project_type`, `language`, and `step_count`
were added in v1.1. Older records without these fields are handled via `.get()` defaults.

Fields `review_cycles_estimated` and `review_cycles_actual` were added in v1.2.
- `review_cycles_estimated`: the N value used when computing the PR Review Loop cost at estimate
  time. Value is 0 when no PR Review Loop step is in scope.
- `review_cycles_actual`: always `null` in v1.2. Reserved for a future feature that will
  count actual review iterations from session logs. Older records without this field are
  treated as `null` via `.get()` defaults.

Fields added in v1.4.0:
- `step_costs_estimated`: dict of {step_name: calibrated_expected_cost} from the
  estimate. Derived from the `step_costs` field in `active-estimate.json`, with the
  PR Review Loop entry excluded. The rename from `step_costs` to `step_costs_estimated`
  distinguishes the estimate-time snapshot from any future actual-cost-per-step field.
  This field
  is diagnostic only — stored for inspection and debugging. It is NOT used by
  update-factors.py for factor computation. Factor computation uses `step_ratios`
  exclusively. Absent in records from v1.3.x and earlier; handled via .get() defaults.
- `step_ratios`: dict of {step_name: ratio} where ratio = actual_cost / expected_cost
  at the session level (same value for all steps in proportional attribution). Absent
  in older records; handled via .get() defaults.

## Factors Format (factors.json)

```json
{
  "sample_count": 10,
  "total_records": 12,
  "outlier_count": 2,
  "outliers": [
    {"timestamp": "2026-03-01T10:00:00Z", "size": "S", "ratio": 4.5, "expected_cost": 1.0, "actual_cost": 4.5},
    {"timestamp": "2026-03-02T12:00:00Z", "size": "M", "ratio": 0.1, "expected_cost": 5.0, "actual_cost": 0.5}
  ],
  "status": "active",
  "global": 1.12,
  "M": 1.18,
  "M_n": 7,
  "S": 0.95,
  "S_n": 3
}
```

- `sample_count`: clean records used for factor computation (excludes outliers)
- `total_records`: all valid records in history (before outlier filtering)
- `outlier_count`: number of records excluded as outliers
- `outliers`: array of excluded records with metadata for inspection
- `step_factors`: per-step correction factors. Each entry has factor (float), n (sample
  count), and status ("collecting" if n < 3, "active" if n >= 3). Absent when no
  step_ratios data has been recorded.

Example `step_factors` entry in factors.json:
```json
"step_factors": {
  "Research Agent": {"factor": 0.82, "n": 5, "status": "active"},
  "Implementation": {"factor": 1.0, "n": 2, "status": "collecting"}
}
```

## Outlier Handling

Records with an actual/expected ratio above 3.0 or below 0.2 are flagged as
outliers and excluded from factor computation. This prevents sessions with
anomalous cost (e.g., runaway loops, aborted sessions) from corrupting
calibration.

**Thresholds:**
- `OUTLIER_HIGH = 3.0` — actual cost was 3x+ the estimate
- `OUTLIER_LOW = 0.2` — actual cost was less than 20% of the estimate

**Behavior:**
- Outliers are logged to stderr during factor computation
- Outlier details are persisted in `factors.json` for inspection
- If all records are outliers, `sample_count = 0` and status remains "collecting"
- Outlier thresholds are not configurable in v1.1 (hardcoded constants)

## Reset

To clear calibration data and start fresh:
```bash
rm calibration/history.jsonl calibration/factors.json
```
