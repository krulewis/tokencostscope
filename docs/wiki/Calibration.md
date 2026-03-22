# Calibration

tokencostscope learns from your sessions over time. No manual tuning needed — the more sessions it observes, the more accurate its estimates become.

---

## How It Learns

At the end of every Claude Code session, the `Stop` hook automatically:

1. Reads `calibration/active-estimate.json` (written when the estimate was produced)
2. Finds the session's JSONL log (`~/.claude/projects/.../session.jsonl`)
3. Parses actual token usage (minus baseline tokens spent before the estimate)
4. Computes `ratio = actual_cost / expected_cost`
5. Appends a record to `calibration/history.jsonl`
6. Runs `update-factors.py` to recompute `calibration/factors.json`

The next estimate automatically loads the updated factors.

---

## Calibration Phases

| Sessions | Behavior |
|----------|----------|
| 0–2      | No correction applied. Output shows "no prior data — will learn after this session" |
| 3–10     | Global correction factor via **trimmed mean** of actual/expected ratios (trim 10% each tail). Time-decay weighting begins (30-day halflife) once 5+ records exist. |
| 10+      | **EWMA** (exponentially weighted moving average) with recency weighting. Per-size-class factors (`XS`, `S`, `M`, `L`) activate when a class has 3+ samples. Per-step factors activate when a step has 3+ samples. Per-signature factors activate when a signature has 3+ samples. |

---

## Time-Decay Weighting

Older calibration records lose influence over time. Each record is weighted by an exponential decay function based on how long ago the session ran:

```
weight = exp(−ln(2) / halflife × days_elapsed)
```

With a 30-day halflife:
- A 30-day-old record has 50% of the influence of a fresh record
- A 60-day-old record has 25% influence
- Older records are never deleted — your full history is preserved

**Cold-start guard:** Decay weighting only applies when 5 or more records exist in a calibration stratum (size-class, step, or signature). Below that threshold, all weights are 1.0 (equal influence). This prevents pathological down-weighting in the early stages of learning.

---

## Per-Signature Calibration

A pipeline signature is a normalized hash of the ordered sequence of pipeline steps. After 3+ runs of the same signature, a per-signature correction factor activates (labeled `P:x` in the Cal column).

This captures cost profiles unique to your workflow. For example:
- An organization that always runs "Research → Architecture → Engineering → QA → Review" might have consistent overestimation in the research phase
- A signature-level factor corrects for this without affecting global or per-step factors

Per-signature factors are computed in **Pass 5** of `update-factors.py` and stored in `factors.json` under `signature_factors`. In the 5-level precedence chain, per-step factors take precedence over per-signature — a per-step factor (labeled `S:x`) overrides a per-signature factor (`P:x`) for the same step when both are active.

---

## Outlier Handling

Sessions with `actual/expected` ratio `> 3.0×` or `< 0.2×` are excluded from calibration. They are logged in `history.jsonl` with a flag and are available for manual inspection, but do not skew the factors.

---

## Calibration Files

All calibration data lives in `calibration/` (gitignored — local to each user):

| File | Purpose |
|------|---------|
| `history.jsonl` | One record per completed session. Each record includes estimate, actual, ratio, size class, pipeline steps, project type, language, parallel groups, and step costs. |
| `factors.json` | Learned correction factors: global, size-class (`M`, `L`, etc.), per-step (`step_factors`), and per-signature (`signature_factors`). |
| `active-estimate.json` | Transient marker written when an estimate is produced; read by learn.sh at session end, then deleted. |
| `.midcheck-state` | Ephemeral state file written by the PreToolUse hook during a session. Tracks last checked byte size and cooldown sentinel. Not part of calibration history. |

---

## Example: factors.json Structure

After several sessions, `factors.json` contains learned factors at multiple levels:

```json
{
  "sample_count": 12,
  "global": 1.12,
  "status": "active",
  "M": 1.08,
  "M_n": 5,
  "L": 1.15,
  "L_n": 4,
  "step_factors": {
    "Research Agent": {"factor": 0.82, "n": 6, "status": "active"},
    "Implementation": {"factor": 1.20, "n": 4, "status": "active"}
  },
  "signature_factors": {
    "research_agent+architect_agent+implementation": {
      "factor": 0.95,
      "n": 3,
      "status": "active"
    }
  }
}
```

The factor selection order is: per-step → per-signature → size-class → global. When a step has 3+ runs, its `S:x` per-step factor takes precedence. When a pipeline signature has 3+ runs but the step lacks a per-step factor, the `P:x` signature factor is used instead.

---

## Sharing Calibration

To share your calibration data with another machine or team member, copy the `calibration/` directory. `history.jsonl` is the source of truth — `factors.json` can be regenerated from it at any time:

```bash
python3 scripts/update-factors.py calibration/history.jsonl calibration/factors.json
```

---

## Stale Pricing Warning

If `references/pricing.md` hasn't been updated in 90+ days, tokencostscope prepends a warning to the output. Check `last_updated` in that file and update prices as needed.
