# How It Works

tokencostscope estimates Claude API cost by decomposing a plan into pipeline steps, computing token counts per step, and applying pricing — before any code is written.

---

## Step-by-Step Algorithm

### Step 0 — Infer Inputs from the Plan

Reads the plan in conversation context and infers:

| Input | How inferred |
|-------|-------------|
| **Size** | Step count: XS (1–2), S (2–3), M (5–8), L (8+) |
| **Files** | File paths or "N files" mentions |
| **Complexity** | Keywords: bug fix → low, new feature → medium, new system → high |
| **Steps** | Maps to canonical pipeline steps in `references/heuristics.md` |
| **Project type** | Keywords: greenfield, refactor, bug_fix, migration, docs |
| **Language** | File extensions: .py, .ts, .go, .rs, etc. |
| **Review cycles** | Inferred from presence of Staff Review + implementation steps (default: 2) |
| **Parallel groups** | Scans for keywords: "in parallel", "simultaneously", "concurrently", "∥" |
| **File sizes** | Auto-measured via `wc -l` when paths are extractable from the plan; `avg_file_lines=` override for new/missing files |

### Step 1 — Load References

Reads pricing and token heuristics from reference files:
- `references/pricing.md` — model prices, cache rates
- `references/heuristics.md` — activity token budgets, pipeline decompositions
- `calibration/factors.json` — learned correction factors (if available)

### Step 2 — Resolve Inputs

Applies overrides if provided. Looks up the complexity multiplier and model for each step.

### Step 3 — Per-Step Calculation

For each pipeline step:

**3a. Base tokens**
```
input_base  = Σ (activity_input_tokens × activity_count)
output_base = Σ (activity_output_tokens × activity_count)
```

**File size bracket adjustment (when file paths are in the plan):**

For file read and file edit activities, `activity_input_tokens` is bracket-dependent:

| Bracket | Line Count | File Read Input | File Edit Input |
|---------|-----------|-----------------|-----------------|
| Small   | ≤ 49      | 3,000           | 1,000           |
| Medium  | 50–500    | 10,000          | 2,500           |
| Large   | ≥ 501     | 20,000          | 5,000           |

Steps where file reads scale with N (Implementation, Test Writing N-writes):
```
file_read_contribution = small_count × 3,000 + medium_count × 10,000 + large_count × 20,000
```
Steps with fixed read counts (Research Agent: 6, Engineer Initial Plan: 4, Engineer Final Plan: 2, QA: 2):
use `avg_file_read_tokens × fixed_count` where `avg_file_read_tokens` is the weighted average
of measured file brackets. If no files measured: `avg_file_read_tokens = 10,000` (identical to prior behavior).

When no file paths are extractable, all files use medium (10,000 tokens) — identical to v1.4.0.

**3b. Complexity**
```
input_complex  = input_base  × complexity_multiplier
output_complex = output_base × complexity_multiplier
```

Multipliers: low=0.7×, medium=1.0×, high=1.5×

**3c. Context accumulation**
```
K           = total activity count in this step
input_accum = input_complex × (K+1)/2
```

Models triangular growth: the first activity sees 1× context, the last sees K×, average is (K+1)/2.

For **parallel steps** (detected from plan text):
```
input_accum = input_accum × 0.75
```
Parallel agents start fresh — no inherited context from prior steps (~25% less effective input).

**3d. Cost per band**
```
cache_rate ← from pricing.md (Optimistic: 60%, Expected: 50%, Pessimistic: 30%)

For parallel steps: cache_rate = max(cache_rate − 0.15, 0.05)

K              = total activity count from Step 3c
cache_write_fraction = 1 / K

input_cost  = (input_accum × (1 − cache_rate) × price_in
            +  input_accum × cache_rate × cache_write_fraction × price_cw
            +  input_accum × cache_rate × (1 − cache_write_fraction) × price_cr) / 1,000,000
output_cost = output_complex × price_out / 1,000,000
step_cost   = (input_cost + output_cost) × band_multiplier
```

The cache cost splits across three terms:
- **Uncached input** at full input price (`price_in`)
- **Cached write** (first turn) at cache write price (`price_cw` — 12.5x read cost)
- **Cached read** (subsequent turns) at cache read price (`price_cr`)

Band multipliers: Optimistic=0.6×, Expected=1.0×, Pessimistic=3.0×

**3e. Calibration (Expected band only)**

Applies a 5-level precedence chain to select the calibration factor:

```
# Pseudocode using actual factors.json keys.
# Per-signature: factors["signature_factors"][signature]["status"] == "active" and n >= 3
# Per-step: factors["step_factors"][step_name]["status"] == "active" and n >= 3
# Size-class: factors[size] exists and factors["{size}_n"] >= 3 (e.g. factors["M_n"])
# Global: factors["global"] exists and factors["status"] == "active"
if step_name in factors["step_factors"] and factors["step_factors"][step_name]["status"] == "active":
  factor = factors["step_factors"][step_name]["factor"]        (Cal: S:x)
elif signature in factors["signature_factors"] and factors["signature_factors"][signature]["status"] == "active":
  factor = factors["signature_factors"][signature]["factor"]   (Cal: P:x)
elif factors[size] exists and factors["{size}_n"] >= 3:
  factor = factors[size]                                       (Cal: Z:x)
elif factors["global"] exists and factors["status"] == "active":
  factor = factors["global"]                                   (Cal: G:x)
else:
  factor = 1.0                                                 (Cal: --)

calibrated_expected    = expected_cost × factor
calibrated_optimistic  = calibrated_expected × 0.6
calibrated_pessimistic = calibrated_expected × 3.0
```

**Calibration source (Cal column in output table):**
- `P:0.79` — per-signature factor applied (3+ runs of the same pipeline signature)
- `S:0.82` — per-step factor applied (3+ sessions recorded for this specific step)
- `Z:0.88` — size-class factor applied (3+ sessions in this size class)
- `G:0.95` — global factor applied (3+ sessions total, but step not yet calibrated)
- `--` — uncalibrated (no factors available; factor = 1.0)

### Per-Signature Calibration

A pipeline signature is a normalized hash of the steps array in the estimate. After 3+ runs of the same signature (e.g., "Planning → Implementation → Review" repeated 3+ times), a per-signature calibration factor activates in the `factors.json` under `signature_factors`. This factor appears in the Cal column as `P:x` and applies before per-step or size-class factors.

Per-signature factors are learned via a dedicated Pass 5 in the calibration algorithm, capturing cost profiles unique to a workflow type. For example, some orgs may consistently over-estimate planning phases while under-estimating QA, resulting in a signature-level correction distinct from global or per-step factors.

### Step 3.5 — PR Review Loop

If the plan includes a Staff Review step, a PR Review Loop row is added using a geometric decay model:

```
C    = staff_review_expected + engineer_final_plan_expected  (un-discounted)
cost = C × (1 − 0.6^N) / 0.4
```

Where N = review cycle count per band (Optimistic=1, Expected=2, Pessimistic=4 by default).

### Step 4 — Output

Sums all step costs per band, renders the table, and writes `calibration/active-estimate.json` for the learning hook to compare against actuals at session end.

---

## Confidence Bands

| Band        | Cache Hit Rate | Multiplier | Meaning |
|-------------|---------------|------------|---------|
| Optimistic  | 60%           | 0.6×       | Best case — focused, cache-warm agent work |
| Expected    | 50%           | 1.0×       | Typical run |
| Pessimistic | 30%           | 3.0×       | With rework loops, debugging, retries |

---

---

## Time-Decay Weighting

Calibration records older than a few weeks exert less influence on current estimates. Each record is weighted by an exponential decay function:

```
weight = exp(−ln(2) / halflife × days_elapsed)
```

With a 30-day halflife, a 30-day-old record has 50% influence; a 60-day-old record has 25% influence. This keeps estimates responsive to recent session patterns without discarding historical data.

**Cold-start guard:** When fewer than 5 records are available in a calibration stratum (size-class, step, or signature), decay is not applied — all weights equal 1.0. This prevents pathological down-weighting in early stages.

Records are never deleted — the skill preserves your full history for long-term trend analysis and fallback.

---

## Mid-Session Cost Tracking

As your session progresses, tokencostscope periodically checks actual spend against the pessimistic estimate via a PreToolUse hook. If spend approaches 80% of the pessimistic band, a warning is issued. Warnings are sampled at ~50KB intervals to avoid verbosity.

Example output:
```
⚠ Cost warning: session spend $15.34 is 82% of pessimistic estimate ($18.70)
  Consider pausing to review plan scope or revisit confidence bands.
```

The check is fail-silent — hook failures do not interrupt your work.

---

## Parallel Agent Accounting

When steps are detected as parallel in the plan, two discounts apply:

| Effect | Adjustment |
|--------|-----------|
| Fresh start — no inherited context | `input_accum × 0.75` |
| Cache miss — no warmed prefix | `cache_rate − 0.15` (floor 0.05) |

Parallel groups are shown with `┌│└` brackets in the output table. The PR Review Loop's base cost (`C`) always uses un-discounted constituent costs — review cycles are sequential by nature.

See [[Configuration]] for override options.
