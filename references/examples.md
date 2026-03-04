# Worked Examples

## Example 1: M-size change, 5 files, Medium complexity

**Inputs:** size=M, file_count=5, complexity=Medium (1.0x)

**Pricing used:**
- Sonnet: input $3.00/M, cache_read $0.30/M, output $15.00/M
- Opus:   input $15.00/M, cache_read $1.50/M, output $75.00/M
- Haiku:  input $0.80/M, cache_read $0.08/M, output $4.00/M

---

### Formula (applied to every step)

```
step_input_base     = sum(activity_input × count)
step_output_base    = sum(activity_output × count)
step_input_complex  = step_input_base × complexity_multiplier
step_output_complex = step_output_base × complexity_multiplier
K                   = total activity count in step
step_input_accum    = step_input_complex × (K + 1) / 2

For each band:
  cache_rate  ∈ {optimistic: 0.60, expected: 0.50, pessimistic: 0.30}
  band_mult   ∈ {optimistic: 0.60, expected: 1.00, pessimistic: 3.00}
  input_cost  = (step_input_accum × (1 - cache_rate) × price_input
              +  step_input_accum × cache_rate × price_cache_read) / 1,000,000
  output_cost = step_output_complex × price_output / 1,000,000
  step_cost   = (input_cost + output_cost) × band_mult
```

---

### Step 1 — Research Agent (Sonnet)

Activities: 6 reads, 4 searches, 1 planning, 3 conv turns → K = 14

```
input_base  = 6×10,000 + 4×500 + 1×3,000 + 3×5,000
            = 60,000 + 2,000 + 3,000 + 15,000 = 80,000
output_base = 6×200 + 4×500 + 1×4,000 + 3×1,500
            = 1,200 + 2,000 + 4,000 + 4,500 = 11,700

complexity 1.0x → input_complex = 80,000, output_complex = 11,700

context accumulation: (14+1)/2 = 7.5
input_accum = 80,000 × 7.5 = 600,000

Expected (cache=50%, band=1.0x):
  input_cost  = (600,000×0.50×3.00 + 600,000×0.50×0.30) / 1,000,000
              = (900,000 + 90,000) / 1,000,000 = $0.9900
  output_cost = 11,700 × 15.00 / 1,000,000 = $0.1755
  step_cost   = ($0.9900 + $0.1755) × 1.0 = $1.1655

Optimistic (cache=60%, band=0.6x):
  input_cost  = (600,000×0.40×3.00 + 600,000×0.60×0.30) / 1,000,000
              = (720,000 + 108,000) / 1,000,000 = $0.8280
  step_cost   = ($0.8280 + $0.1755) × 0.6 = $0.6021

Pessimistic (cache=30%, band=3.0x):
  input_cost  = (600,000×0.70×3.00 + 600,000×0.30×0.30) / 1,000,000
              = (1,260,000 + 54,000) / 1,000,000 = $1.3140
  step_cost   = ($1.3140 + $0.1755) × 3.0 = $4.4685
```

---

### Step 2 — Architect Agent (Opus)

Activities: 1 code review, 1 planning, 2 conv turns → K = 4

```
input_base  = 1×8,000 + 1×3,000 + 2×5,000 = 21,000
output_base = 1×3,000 + 1×4,000 + 2×1,500 = 10,000
input_accum = 21,000 × (4+1)/2 = 21,000 × 2.5 = 52,500

Expected:
  input_cost  = (52,500×0.50×15.00 + 52,500×0.50×1.50) / 1,000,000
              = (393,750 + 39,375) / 1,000,000 = $0.4331
  output_cost = 10,000 × 75.00 / 1,000,000 = $0.7500
  step_cost   = $1.1831

Optimistic:
  input_cost  = (52,500×0.40×15.00 + 52,500×0.60×1.50) / 1,000,000
              = (315,000 + 47,250) / 1,000,000 = $0.3623
  step_cost   = ($0.3623 + $0.7500) × 0.6 = $0.6674

Pessimistic:
  input_cost  = (52,500×0.70×15.00 + 52,500×0.30×1.50) / 1,000,000
              = (551,250 + 23,625) / 1,000,000 = $0.5749
  step_cost   = ($0.5749 + $0.7500) × 3.0 = $3.9746
```

---

### Step 3 — Engineer Initial Plan (Sonnet)

Activities: 4 reads, 2 searches, 1 planning, 2 conv turns → K = 9

```
input_base  = 4×10,000 + 2×500 + 1×3,000 + 2×5,000 = 54,000
output_base = 4×200 + 2×500 + 1×4,000 + 2×1,500 = 8,800
input_accum = 54,000 × (9+1)/2 = 54,000 × 5.0 = 270,000

Expected:
  input_cost  = (270,000×0.50×3.00 + 270,000×0.50×0.30) / 1,000,000 = $0.4455
  output_cost = 8,800 × 15.00 / 1,000,000 = $0.1320
  step_cost   = $0.5775

Optimistic:
  input_cost  = (270,000×0.40×3.00 + 270,000×0.60×0.30) / 1,000,000 = $0.3726
  step_cost   = ($0.3726 + $0.1320) × 0.6 = $0.3028

Pessimistic:
  input_cost  = (270,000×0.70×3.00 + 270,000×0.30×0.30) / 1,000,000 = $0.5913
  step_cost   = ($0.5913 + $0.1320) × 3.0 = $2.1699
```

---

### Step 4 — Staff Review (Opus)

Activities: 1 code review, 2 conv turns → K = 3
(Code review pass already includes reading the diff/files — no separate reads.)

```
input_base  = 1×8,000 + 2×5,000 = 18,000
output_base = 1×3,000 + 2×1,500 = 6,000
input_accum = 18,000 × (3+1)/2 = 18,000 × 2.0 = 36,000

Expected:
  input_cost  = (36,000×0.50×15.00 + 36,000×0.50×1.50) / 1,000,000 = $0.2970
  output_cost = 6,000 × 75.00 / 1,000,000 = $0.4500
  step_cost   = $0.7470

Optimistic:
  input_cost  = (36,000×0.40×15.00 + 36,000×0.60×1.50) / 1,000,000 = $0.2484
  step_cost   = ($0.2484 + $0.4500) × 0.6 = $0.4190

Pessimistic:
  input_cost  = (36,000×0.70×15.00 + 36,000×0.30×1.50) / 1,000,000 = $0.3942
  step_cost   = ($0.3942 + $0.4500) × 3.0 = $2.5326
```

---

### Step 5 — Engineer Final Plan (Sonnet)

Activities: 2 reads, 1 planning, 2 conv turns → K = 5

```
input_base  = 2×10,000 + 1×3,000 + 2×5,000 = 33,000
output_base = 2×200 + 1×4,000 + 2×1,500 = 7,400
input_accum = 33,000 × (5+1)/2 = 33,000 × 3.0 = 99,000

Expected:
  input_cost  = (99,000×0.50×3.00 + 99,000×0.50×0.30) / 1,000,000 = $0.1634
  output_cost = 7,400 × 15.00 / 1,000,000 = $0.1110
  step_cost   = $0.2744

Optimistic:
  input_cost  = (99,000×0.40×3.00 + 99,000×0.60×0.30) / 1,000,000 = $0.1366
  step_cost   = ($0.1366 + $0.1110) × 0.6 = $0.1486

Pessimistic:
  input_cost  = (99,000×0.70×3.00 + 99,000×0.30×0.30) / 1,000,000 = $0.2168
  step_cost   = ($0.2168 + $0.1110) × 3.0 = $0.9834
```

---

### Step 6 — Test Writing (Sonnet)

Activities: 3 reads, N=5 test writes, 3 conv turns → K = 11

```
input_base  = 3×10,000 + 5×2,000 + 3×5,000 = 55,000
output_base = 3×200 + 5×5,000 + 3×1,500 = 30,100
input_accum = 55,000 × (11+1)/2 = 55,000 × 6.0 = 330,000

Expected:
  input_cost  = (330,000×0.50×3.00 + 330,000×0.50×0.30) / 1,000,000 = $0.5445
  output_cost = 30,100 × 15.00 / 1,000,000 = $0.4515
  step_cost   = $0.9960

Optimistic:
  input_cost  = (330,000×0.40×3.00 + 330,000×0.60×0.30) / 1,000,000 = $0.4554
  step_cost   = ($0.4554 + $0.4515) × 0.6 = $0.5441

Pessimistic:
  input_cost  = (330,000×0.70×3.00 + 330,000×0.30×0.30) / 1,000,000 = $0.7227
  step_cost   = ($0.7227 + $0.4515) × 3.0 = $3.5226
```

---

### Step 7 — Implementation (Sonnet)

Activities: N=5 reads, N=5 edits, 4 conv turns → K = 14

```
input_base  = 5×10,000 + 5×2,500 + 4×5,000 = 82,500
output_base = 5×200 + 5×1,500 + 4×1,500 = 14,500
input_accum = 82,500 × (14+1)/2 = 82,500 × 7.5 = 618,750

Expected:
  input_cost  = (618,750×0.50×3.00 + 618,750×0.50×0.30) / 1,000,000 = $1.0209
  output_cost = 14,500 × 15.00 / 1,000,000 = $0.2175
  step_cost   = $1.2384

Optimistic:
  input_cost  = (618,750×0.40×3.00 + 618,750×0.60×0.30) / 1,000,000 = $0.8539
  step_cost   = ($0.8539 + $0.2175) × 0.6 = $0.6428

Pessimistic:
  input_cost  = (618,750×0.70×3.00 + 618,750×0.30×0.30) / 1,000,000 = $1.3551
  step_cost   = ($1.3551 + $0.2175) × 3.0 = $4.7178
```

---

### Step 8 — Playwright QA (Haiku)

Activities: 3 shell commands, 2 reads, 2 conv turns → K = 7

```
input_base  = 3×300 + 2×10,000 + 2×5,000 = 30,900
output_base = 3×500 + 2×200 + 2×1,500 = 4,900
input_accum = 30,900 × (7+1)/2 = 30,900 × 4.0 = 123,600

Expected:
  input_cost  = (123,600×0.50×0.80 + 123,600×0.50×0.08) / 1,000,000 = $0.0544
  output_cost = 4,900 × 4.00 / 1,000,000 = $0.0196
  step_cost   = $0.0740

Optimistic:
  input_cost  = (123,600×0.40×0.80 + 123,600×0.60×0.08) / 1,000,000 = $0.0455
  step_cost   = ($0.0455 + $0.0196) × 0.6 = $0.0390

Pessimistic:
  input_cost  = (123,600×0.70×0.80 + 123,600×0.30×0.08) / 1,000,000 = $0.0722
  step_cost   = ($0.0722 + $0.0196) × 3.0 = $0.2754
```

---

### Final Summary — M-size, 5 files, Medium complexity

| Step                  | Model  | Optimistic | Expected | Pessimistic |
|-----------------------|--------|------------|----------|-------------|
| Research Agent        | Sonnet | $0.60      | $1.17    | $4.47       |
| Architect Agent       | Opus   | $0.67      | $1.18    | $3.97       |
| Engineer Initial Plan | Sonnet | $0.30      | $0.58    | $2.17       |
| Staff Review          | Opus   | $0.42      | $0.75    | $2.53       |
| Engineer Final Plan   | Sonnet | $0.15      | $0.27    | $0.98       |
| Test Writing          | Sonnet | $0.54      | $1.00    | $3.52       |
| Implementation        | Sonnet | $0.64      | $1.24    | $4.72       |
| Playwright QA         | Haiku  | $0.04      | $0.07    | $0.28       |
| **TOTAL**             |        | **$3.37**  | **$6.26**| **$22.64**  |

Pessimistic band includes rework loops, repeated reads, and debugging cycles.

---

## Example 2: M-size change with PR Review Loop, N=2 expected cycles

This example extends Example 1 by adding a PR Review Loop step with N=2 expected review
cycles. All prior step costs are unchanged. This example uses pre-calibration values
(calibration factor = 1.0).

**Inputs:** size=M, file_count=5, complexity=Medium (1.0x), review_cycles=2

**Constituent steps for C (pre-calibration Expected costs from Example 1 Step 3d):**
- Staff Review Expected: $0.7470
- Engineer Final Plan Expected: $0.2744

```
C = staff_review_expected + engineer_final_plan_expected
C = $0.7470 + $0.2744 = $1.0214
```

**Per-band cycle counts:**
- Optimistic: 1 cycle (best case: first pass clears all issues)
- Expected: N=2 cycles
- Pessimistic: N×2 = 4 cycles

**PR Review Loop cost formula:**
```
review_loop_cost(cycles) = C × (1 − 0.6^cycles) / 0.4
```

**Optimistic (1 cycle):**
```
review_loop_cost = $1.0214 × (1 − 0.6^1) / 0.4
                 = $1.0214 × (1 − 0.6) / 0.4
                 = $1.0214 × 0.4 / 0.4
                 = $1.0214 × 1.0
                 = $1.0214
```

**Expected (2 cycles):**
```
review_loop_cost = $1.0214 × (1 − 0.6^2) / 0.4
                 = $1.0214 × (1 − 0.36) / 0.4
                 = $1.0214 × 0.64 / 0.4
                 = $1.0214 × 1.6
                 = $1.6342
```

**Pessimistic (4 cycles):**
```
review_loop_cost = $1.0214 × (1 − 0.6^4) / 0.4
                 = $1.0214 × (1 − 0.1296) / 0.4
                 = $1.0214 × 0.8704 / 0.4
                 = $1.0214 × 2.176
                 = $2.2226
```

**Calibration (factor = 1.0, no prior data):**
```
calibrated_expected    = $1.6342 × 1.0 = $1.6342
calibrated_optimistic  = $1.6342 × 0.6 = $0.9805
calibrated_pessimistic = $1.6342 × 3.0 = $4.9027
```

Note: With factor=1.0 the calibrated values equal the re-anchored bands. The calibrated
optimistic ($0.9805) and pessimistic ($4.9027) differ from the raw formula values
($1.0214 and $2.2226) because calibration re-anchors all bands around the calibrated
Expected center, not the raw per-band values.

### Final Summary — M-size, 5 files, Medium complexity, N=2 review cycles

| Step                  | Model       | Optimistic | Expected | Pessimistic |
|-----------------------|-------------|------------|----------|-------------|
| Research Agent        | Sonnet      | $0.60      | $1.17    | $4.47       |
| Architect Agent       | Opus        | $0.67      | $1.18    | $3.97       |
| Engineer Initial Plan | Sonnet      | $0.30      | $0.58    | $2.17       |
| Staff Review          | Opus        | $0.42      | $0.75    | $2.53       |
| Engineer Final Plan   | Sonnet      | $0.15      | $0.27    | $0.98       |
| Test Writing          | Sonnet      | $0.54      | $1.00    | $3.52       |
| Implementation        | Sonnet      | $0.64      | $1.24    | $4.72       |
| Playwright QA         | Haiku       | $0.04      | $0.07    | $0.28       |
| PR Review Loop        | Opus+Sonnet | $0.98      | $1.63    | $4.90       |
| **TOTAL**             |             | **$4.35**  | **$7.89**| **$27.54**  |

**Bands:** Optimistic (1 review cycle) · Expected (2 cycles) · Pessimistic (4 cycles)
