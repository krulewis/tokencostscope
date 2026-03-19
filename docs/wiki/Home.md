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
## costscope estimate (v1.3.1)

Change: size=M, files=5, complexity=medium, type=greenfield, lang=python
Steps: all (8 steps)
Calibration: 1.12x from 8 prior runs

| Step                  | Model       | Optimistic | Expected | Pessimistic |
|-----------------------|-------------|------------|----------|-------------|
| ┌ Parallel Group 1 ∥  |             |            |          |             |
| │ Research Agent      | Sonnet      | $0.38      | $0.71    | $2.13       |
| └ PM Agent            | Opus        | $0.41      | $0.72    | $2.17       |
| Architect Agent       | Opus        | $0.67      | $1.18    | $3.97       |
| ...                   | ...         | ...        | ...      | ...         |
| PR Review Loop        | Opus+Sonnet | $1.02      | $1.63    | $2.22       |
| **TOTAL**             |             | **$3.37**  | **$6.26**| **$22.64**  |
```

Parallel steps detected in the plan are grouped and discounted automatically — parallel agents start fresh (no inherited context) and miss the warmed cache.

---

## How It Gets Smarter

Every session end, the learning hook reads the JSONL log, computes actual cost, and updates calibration factors. After 3+ sessions, estimates are corrected by a learned multiplier. After 10+ sessions, per-size-class EWMA factors kick in.

No configuration required — it just learns.
