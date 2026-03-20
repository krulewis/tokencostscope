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

Applies a 4-level precedence chain to select the calibration factor:

```
# Pseudocode — simplified property names.
# Actual factors.json keys: "step_factors", "size_class_factors", "global_factor"
if step_name in factors.step_factors:
  factor = factors.step_factors[step_name].factor       (Cal: S:x)
elif size_class in factors.size_class_factors:
  factor = factors.size_class_factors[size_class].factor (Cal: Z:x)
elif factors.global_factor exists:
  factor = factors.global_factor                          (Cal: G:x)
else:
  factor = 1.0                                            (Cal: --)

calibrated_expected    = expected_cost × factor
calibrated_optimistic  = calibrated_expected × 0.6
calibrated_pessimistic = calibrated_expected × 3.0
```

**Calibration source (Cal column in output table):**
- `S:0.82` — per-step factor applied (3+ sessions recorded for this specific step)
- `Z:0.88` — size-class factor applied (10+ sessions in this size class)
- `G:0.95` — global factor applied (3+ sessions total, but step not yet calibrated)
- `--` — uncalibrated (no factors available; factor = 1.0)

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

## Parallel Agent Accounting

When steps are detected as parallel in the plan, two discounts apply:

| Effect | Adjustment |
|--------|-----------|
| Fresh start — no inherited context | `input_accum × 0.75` |
| Cache miss — no warmed prefix | `cache_rate − 0.15` (floor 0.05) |

Parallel groups are shown with `┌│└` brackets in the output table. The PR Review Loop's base cost (`C`) always uses un-discounted constituent costs — review cycles are sequential by nature.

See [[Configuration]] for override options.
