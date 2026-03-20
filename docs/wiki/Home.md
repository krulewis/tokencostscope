# tokencostscope

A Claude Code skill that estimates Anthropic API cost for planned agent tasks, then **learns from actual usage** to improve estimates over time.

Install once per project. It auto-estimates after plans are created and auto-learns at session end. Zero ongoing friction.

---

## Pages

- [[Installation]] — Clone, install hooks, verify setup
- [[How It Works]] — Estimation algorithm, formulas, confidence bands
- [[Calibration]] — How the skill learns from your sessions over time
- [[Configuration]] — Manual overrides, parallel agent accounting
- [[Roadmap]] — Planned features and version history

---

## Quick Look

After a plan is created, tokencostscope automatically outputs a cost table:

```
## costscope estimate (v1.4.0)

Change: size=M, files=5, complexity=medium, type=greenfield, lang=python
Steps: all (8 steps)
Calibration: size-class M=1.12x (8 runs) | global 1.12x (8 runs)

| Step                  | Model       | Cal    | Optimistic | Expected | Pessimistic |
|-----------------------|-------------|--------|------------|----------|-------------|
| ┌ Parallel Group 1 ∥  |             |        |            |          |             |
| │ Research Agent      | Sonnet      | S:0.82 | $0.38      | $0.71    | $2.13       |
| └ PM Agent            | Opus        | Z:0.88 | $0.41      | $0.72    | $2.17       |
| Architect Agent       | Opus        | G:0.95 | $0.67      | $1.18    | $3.97       |
| ...                   | ...         | ...    | ...        | ...      | ...         |
| PR Review Loop        | Opus+Sonnet | --     | $1.02      | $1.63    | $2.22       |
| **TOTAL**             |             |        | **$3.37**  | **$6.26**| **$22.64**  |
```

Parallel steps detected in the plan are grouped and discounted automatically — parallel agents start fresh (no inherited context) and miss the warmed cache.

The **Cal** column shows the calibration source: `S:x` = per-step factor, `Z:x` = size-class factor, `G:x` = global factor, `--` = uncalibrated.

---

## How It Gets Smarter

Every session end, the learning hook reads the JSONL log, computes actual cost, and updates calibration factors. After 3+ sessions, estimates are corrected by a learned multiplier. After 10+ sessions, per-size-class EWMA factors kick in. After 3+ sessions per step, per-step correction factors activate — letting the skill distinguish between overestimated steps (e.g., Research) and underestimated steps (e.g., QA).

No configuration required — it just learns.
