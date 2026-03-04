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
A single global correction factor computed as the **median** of all
actual/expected ratios. Median is used over mean to resist outliers.

```
factor = median(actual_cost / expected_cost for all records)
```

### Phase 3: Stratified (10+ samples globally, 3+ per stratum)
Per-size-class factors computed independently. Each stratum (XS, S, M, L)
that has 3+ samples gets its own factor. Strata below 3 samples fall back
to the global factor.

For strata with 10+ samples, switches from median to EWMA (alpha=0.15)
for recency weighting:

```
ewma[0] = ratios[0]
ewma[i] = 0.15 × ratios[i] + 0.85 × ewma[i-1]
factor  = ewma[last]
```

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
  "turn_count": 48
}
```

## Factors Format (factors.json)

```json
{
  "sample_count": 12,
  "status": "active",
  "global": 1.12,
  "M": 1.18,
  "M_n": 8,
  "S": 0.95,
  "S_n": 4
}
```

## Reset

To clear calibration data and start fresh:
```bash
rm calibration/history.jsonl calibration/factors.json
```
