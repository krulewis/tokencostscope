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
| 3–10     | Global correction factor via **trimmed mean** of actual/expected ratios (trim 10% each tail) |
| 10+      | **EWMA** (exponentially weighted moving average) with recency weighting. Per-size-class factors (`XS`, `S`, `M`, `L`) activate when a class has 3+ samples |

---

## Outlier Handling

Sessions with `actual/expected` ratio `> 3.0×` or `< 0.2×` are excluded from calibration. They are logged in `history.jsonl` with a flag and are available for manual inspection, but do not skew the factors.

---

## Calibration Files

All calibration data lives in `calibration/` (gitignored — local to each user):

| File | Purpose |
|------|---------|
| `history.jsonl` | One record per completed session. Each record includes estimate, actual, ratio, size class, pipeline steps, project type, language, and (v1.3+) parallel groups. |
| `factors.json` | Learned correction factors keyed by size class. |
| `active-estimate.json` | Transient marker written when an estimate is produced; deleted after learning. |

---

## Sharing Calibration

To share your calibration data with another machine or team member, copy the `calibration/` directory. `history.jsonl` is the source of truth — `factors.json` can be regenerated from it at any time:

```bash
python3 scripts/update-factors.py calibration/history.jsonl calibration/factors.json
```

---

## Stale Pricing Warning

If `references/pricing.md` hasn't been updated in 90+ days, tokencostscope prepends a warning to the output. Check `last_updated` in that file and update prices as needed.
