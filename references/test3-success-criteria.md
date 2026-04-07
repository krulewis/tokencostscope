# Test 3 Success Criteria — tokencast v0.1.6

**Test 3** measures whether tokencast's calibration loop is working in practice:
users run `estimate_cost` before a session, then close the loop with `report_session`
so that actuals feed back into calibration. The 4-week measurement window started
**2026-03-31** and closes **2026-04-28**.

These criteria were defined before the window opened so results can be interpreted
without post-hoc target-setting. They are documented here (in `references/`) so
that developers and the `/tokencast status` tool can reference them directly.

---

## Criteria Summary

| Criterion | Key | Target |
|---|---|---|
| (a) Engaged calibration ratio | `report_session_ratio_min` | 0.5 |
| (b) Statistical confidence | `calibration_records_min` | 10 |
| (c) Band accuracy | `band_hit_rate_min` | 0.80 |
| (d) Quality install count | `quality_install_min_installs` | 100 |
| (d) Sessions per quality install | `quality_install_min_sessions` | 3 |

---

## Machine-Readable Thresholds

```
report_session_ratio_min = 0.5
calibration_records_min = 10
band_hit_rate_min = 0.80
quality_install_min_installs = 100
quality_install_min_sessions = 3
```

---

## (a) Engaged Calibration Ratio

**Key:** `report_session_ratio_min = 0.5`

**Definition:** For a given install, the ratio of `report_session` calls to
`estimate_cost` calls over the measurement window must be ≥ 0.5 to count as
"engaged calibration" — i.e., the user closes the feedback loop for at least
1 in 2 estimates.

**Rationale:** The calibration loop only learns when `report_session` is called
after `estimate_cost`. A ratio below 0.5 means most estimates are never closed
out, so the tool cannot improve for that user. 0.5 is intentionally conservative:
users may legitimately skip `report_session` when a session is interrupted or
spans multiple Claude Code sessions. Below 0.3 would indicate the nudge is not
working at all; above 0.7 would indicate strong engagement.

**Test 3 pass:** ≥ 25% of active installs (those with ≥ 3 `estimate_cost` calls)
have `report_session_ratio_min ≥ 0.5`.

---

## (b) Statistical Confidence — Minimum Sample Count

**Key:** `calibration_records_min = 10`

**Definition:** An install must have ≥ 10 clean (non-outlier) calibration records
in `history.jsonl` before its factor can be considered statistically meaningful
for Test 3 evaluation.

**Rationale:** The calibration algorithm activates factors at 3 samples (the
`per_step_min_samples` threshold). That is sufficient to produce a factor, but
not sufficient for statistical confidence in Test 3 analysis. 10 records gives
enough data to distinguish signal from noise in the actual/expected ratio while
remaining achievable within a 4-week window for active users (≈ 2–3 sessions/week).

**Relationship to algorithm constants:**
- `per_step_min_samples = 3` — algorithm activation (algorithm constant, not a Test 3 target)
- `calibration_records_min = 10` — Test 3 statistical confidence bar (this document)

**Test 3 pass:** ≥ 10 installs each have ≥ 10 clean calibration records by
window close (2026-04-28).

---

## (c) Band Accuracy

**Key:** `band_hit_rate_min = 0.80`

**Definition:** At least 80% of actual session costs must land within the
`[optimistic_cost, pessimistic_cost]` band produced by `estimate_cost`.
Measured across all calibration records in `history.jsonl` that include stored
`optimistic_cost` and `pessimistic_cost` fields (v1.4.0+).

**Rationale:** The optimistic/pessimistic band is the primary accuracy signal
users see. If fewer than 80% of actuals land inside the band, either the band
width (0.6×–3.0×) is too narrow for real workloads, or the Expected estimate
is systematically off in one direction. 80% is achievable for a 0.6×–3.0×
width band without calibration; falling below 80% indicates a structural problem.

**Computation:** `band_hit_rate = count(optimistic_cost ≤ actual_cost ≤ pessimistic_cost) / total_records`

Records missing `optimistic_cost` or `pessimistic_cost` are excluded from the
denominator (graceful degradation for pre-v1.4.0 records).

**Test 3 pass:** Global `band_hit_rate ≥ 0.80` across all eligible records in
the measurement window.

---

## (d) Quality Install Sub-Metric

**Keys:** `quality_install_min_installs = 100`, `quality_install_min_sessions = 3`

**Definition:** A "quality install" is an install that has recorded ≥ 3 sessions
(i.e., called `report_session` at least 3 times). The Test 3 install gate
requires ≥ 100 quality installs.

**Rationale:** Raw install count (PyPI downloads) is gamed by bots, CI pipelines,
and `pip install` without ever running the tool. 3 sessions signals genuine
ongoing use — the user has integrated tokencast into their workflow, not just
tried it once. This definition was established in ROADMAP.md v0.1.5.

**Relationship to telemetry:** `session_count` in PostHog events (from
`telemetry.collect_metrics`) records the number of history records per install
at the time of each event. An install has ≥ 3 sessions when any of its events
report `session_count ≥ 3`.

**Test 3 pass:** ≥ 100 distinct install IDs have `session_count ≥ 3` in PostHog
by window close (2026-04-28).

---

## Interpretation Guide

| Result | Meaning | Action |
|---|---|---|
| All 4 criteria pass | Calibration loop is working; proceed to v1.0 | Advance to next milestone |
| (a) fails only | Nudge not driving report_session adoption | Strengthen nudge in v0.1.7 |
| (b) fails only | Not enough engaged users yet | Extend window or widen distribution |
| (c) fails only | Band width or Expected estimate has structural error | Investigate heuristics, open EWMA investigation |
| (d) fails only | Install base too small for statistical validity | Marketing/distribution push |
| (a) + (b) fail | report_session adoption is the bottleneck | Priority fix: improve nudge |
| All fail | Product-market fit or discoverability problem | Sr. PM review |

---

## References

- `references/calibration-algorithm.md` — `per_step_min_samples`, outlier constants
- `references/heuristics.md` — band multipliers (0.6×, 1.0×, 3.0×)
- `ROADMAP.md` v0.1.5 — quality install definition origin
- `src/tokencast/telemetry.py` — `session_count` metric source
- `scripts/tokencast-status.py` — status tool that will surface these criteria
