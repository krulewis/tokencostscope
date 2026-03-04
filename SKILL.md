---
name: tokencostscope
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
4. **Steps:** Which pipeline steps does the plan cover? Map to canonical names.
5. **Project type:** Infer from plan keywords → `greenfield` (new project/system), `refactor` (restructure/reorganize/simplify), `bug_fix` (fix/broken/regression), `migration` (migrate/upgrade/port), `docs` (documentation/readme). Default: `greenfield`.
6. **Language:** Infer primary language from file extensions in the plan → `.py`→`python`, `.ts/.tsx`→`typescript`, `.js/.jsx`→`javascript`, `.go`→`go`, `.rs`→`rust`, `.rb`→`ruby`, `.java`→`java`, `.sh`→`shell`. If mixed, use the most frequent. Default: `unknown`.

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
```

**3d. Compute cost for each band (Optimistic / Expected / Pessimistic)**
```
cache_rate ← from pricing.md for this band
band_mult  ← from heuristics.md for this band
price_in   ← model input price per million
price_cr   ← model cache_read price per million
price_cw   ← model cache_write price per million
price_out  ← model output price per million

input_cost  = (input_accum × (1 - cache_rate) × price_in
            +  input_accum × cache_rate × price_cr) / 1,000,000
output_cost = output_complex × price_out / 1,000,000
step_cost   = (input_cost + output_cost) × band_mult
```

**3e. Apply calibration factor (Expected band only)**

If `calibration/factors.json` exists and has a factor for this size class:
```
calibrated_expected = expected_cost × calibration_factor
calibrated_optimistic = calibrated_expected × 0.6
calibrated_pessimistic = calibrated_expected × 3.0
```
If no calibration data, use raw values (factor = 1.0).

## Step 4 — Sum, Format, and Record

Sum step costs across all in-scope steps for each band. Render the output template.

### Compute baseline_cost

Before writing the estimate, compute the session's cost so far (baseline):
```
Find the current session JSONL:
  find ~/.claude/projects/ -name "*.jsonl" -type f -print0 | xargs -0 ls -t | head -1

Run: python3 scripts/sum-session-tokens.py <session-jsonl> 0
Use the returned total_session_cost as baseline_cost. If the command fails, use 0.
```

Then write the estimate marker for the learning system:
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
  "baseline_cost": <baseline_cost>
}
```

## Output Template

```
## costscope estimate

**Change:** size={size}, files={N}, complexity={complexity}, type={project_type}, lang={language}
**Steps:** {all | list of included steps} ({step_count} steps)
**Pricing:** last updated {last_updated}
**Calibration:** {factor}x from {N} prior runs | or "no prior data — will learn after this session"
{WARNING line if pricing stale}

| Step                  | Model  | Optimistic | Expected | Pessimistic |
|-----------------------|--------|------------|----------|-------------|
| Research Agent        | Sonnet | $X.XX      | $X.XX    | $X.XX       |
| ...                   | ...    | ...        | ...      | ...         |
| **TOTAL**             |        | **$X.XX**  | **$X.XX**| **$X.XX**   |

**Bands:** Optimistic (best case) · Expected (typical) · Pessimistic (with rework)
**Tracking:** Estimate recorded. Actuals will be captured automatically at session end.
```

## Overrides (manual invocation only)

| Override | Effect |
|----------|--------|
| `size=M` | Set size class explicitly |
| `files=5` | Set file count explicitly |
| `complexity=high` | Set complexity explicitly |
| `steps=implement,test,qa` | Estimate only those pipeline steps |
| `project_type=migration` | Set project type explicitly |
| `language=go` | Set primary language explicitly |

## Limitations

- Heuristics assume the global CLAUDE.md workflow. Non-standard workflows will differ.
- Token counts assume typical 150-300 line source files.
- Does not model parallel agent execution (treated as sequential).
- Calibration requires 3+ completed sessions before corrections activate.
- Pricing data may be stale; check `last_updated` in references/pricing.md.
