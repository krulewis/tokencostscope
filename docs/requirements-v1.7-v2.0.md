# Requirements: v1.7 Per-Agent Step Actuals + v2.0 /tokencostscope status

## Clarified Intent

The user wants a cost management dashboard (`/tokencostscope status`) that answers two questions:

1. **Are my estimates accurate?** Is accuracy improving or worsening over time?
2. **Where is my money going?** Which pipeline steps are the biggest cost drivers, and what can be done about it?

The status command requires *true per-step actual costs* — estimated cost distribution is not sufficient for cost attribution. This means v1.7 (per-agent step tagging) must ship before v2.0 (status command) can deliver its full value.

Both features are scoped here as a single requirements document because v2.0 depends on v1.7 data.

---

## v1.7 — Per-Agent Step Actuals

### What It Does

Captures actual token cost per named agent (pipeline step) during a session, enabling per-step actual/expected comparison. Currently, actual cost is only available at the session level.

### Mechanism

- **Hook-based step tagging:** PreToolUse and PostToolUse hooks on the Agent tool record when each named agent starts and stops.
- **Sidecar timeline file:** A JSON events file written alongside the session JSONL. Each event records agent name, start/stop timestamp, and byte offset into the JSONL. At session end, `sum-session-tokens.py` uses these offsets to attribute token costs to each agent span.
- **Granularity:** Agent-level only (v1.7). Tool-level tracking within agents (file reads, searches, etc.) is deferred to a future version.
- **Extensibility:** The sidecar format must be extensible JSON events so tool-level granularity can be added later without schema breaks.

### Success Criteria

- SC-1: After a session with 3+ named agents, `sum-session-tokens.py` produces a per-agent cost breakdown that sums to the session total (within rounding tolerance).
- SC-2: `history.jsonl` records gain a `step_actuals` field: `{"Research Agent": 2.31, "Implementation": 4.56, ...}`.
- SC-3: `update-factors.py` uses true per-step ratios (actual_step_cost / expected_step_cost) instead of proportional attribution for per-step factor computation.
- SC-4: Sidecar file is created/appended atomically and does not corrupt if the session crashes mid-agent.
- SC-5: Sessions without step tagging (pre-v1.7 records, sessions where hooks didn't fire) degrade gracefully — proportional attribution remains the fallback.
- SC-6: Sidecar format includes a `version` field and uses extensible JSON events (one JSON object per line) so tool-level events can be added in a future version.

### Constraints

- Must not add measurable latency to tool calls (hook overhead target: < 5ms per event).
- Must not break existing `learn.sh` / `update-factors.py` pipeline for sessions that lack sidecar data.
- Sidecar file location: `calibration/` directory (gitignored, same lifecycle as other calibration data).

### Edge Cases

- **Nested agents:** If an agent spawns sub-agents, each sub-agent gets its own event. Parent agent's cost excludes sub-agent spans (no double-counting).
- **Overlapping/parallel agents:** Multiple agents running simultaneously each get their own spans. Token attribution uses JSONL byte offsets, not wall-clock time, to avoid overlap ambiguity.
- **Agent without a canonical step name:** Agents with names not matching any canonical step in heuristics.md are recorded as-is. The status command groups them under "Other."
- **No agents in session:** If no Agent tool calls occur (e.g., XS inline work), no sidecar is created. Session falls back to session-level-only tracking.

---

## v2.0 — /tokencostscope status

### What It Does

A cost management dashboard invoked as `/tokencostscope status`. Analyzes calibration history, surfaces accuracy trends, attributes cost to pipeline steps, and makes actionable recommendations the user can apply interactively.

### Architecture

- **Python analysis script:** `scripts/tokencostscope-status.py` reads `history.jsonl`, `factors.json`, and `heuristics.md`. Outputs structured JSON.
- **SKILL.md integration:** SKILL.md handles formatting the JSON output into the markdown dashboard and managing the interactive "Apply?" conversation flow.

### Invocation

```
/tokencostscope status                    # default: adaptive window, interactive
/tokencostscope status window=30d         # override: last 30 days
/tokencostscope status window=10          # override: last 10 sessions
/tokencostscope status --verbose          # show partial data even when sparse
/tokencostscope status --json             # structured JSON output, no formatting
/tokencostscope status --no-apply         # show recommendations but skip prompts
```

### Output Sections (in order)

1. **Health Summary** — one-line calibration status
   - Example: "Calibration: active, 3 clean samples, global factor 1.26x"
   - Shows: status (collecting/active), clean sample count, active factor level (per-step/per-signature/size-class/global), factor value

2. **Accuracy Trend** — recent session ratios with trend direction
   - Shows actual/expected ratios for sessions in the time window
   - Trend classification: improving (ratios converging toward 1.0), stable (within +/-10%), degrading (ratios diverging)
   - Includes: mean ratio, median ratio, % of sessions within Expected band, % within Pessimistic band

3. **Cost Attribution** — per-step actual spend breakdown (requires v1.7 data)
   - Shows each pipeline step's actual cost as absolute dollars and percentage of total
   - Aggregated across sessions in the time window
   - Sorted by cost (highest first)
   - Includes per-step accuracy: actual vs. estimated for each step

4. **Outlier Report** — flagged sessions with context
   - Shows sessions excluded from calibration (ratio > 3.0 or < 0.2)
   - Includes: timestamp, size, ratio, expected vs actual cost, notes field if present
   - Highlights patterns: "3 of 5 outliers are from sessions where all work ran on Opus instead of estimated Sonnet"

5. **Recommendations** — actionable suggestions with "Apply? [y/N]"
   - Each recommendation includes: plain-language description, supporting data, proposed action
   - Interactive prompt to apply each recommendation
   - See Recommendations section below for scope

### Time Window

- **Adaptive default:** History with 10 or fewer sessions shows all sessions. History with more than 10 sessions shows a rolling window (last 30 days or last 10 sessions, whichever is larger).
- **User override:** `window=30d` (time-based) or `window=10` (count-based).

### Sparse/Empty History Behavior

- **No history.jsonl:** Display "Not enough data yet. Complete 3+ sessions to activate calibration and status analysis."
- **1-2 records:** Display "Not enough data yet. {N} session(s) recorded, {3-N} more needed for calibration to activate."
- **All records are outliers (sample_count=0):** Display "Not enough data yet. {total} sessions recorded but all flagged as outliers — no clean data for calibration."
- **`--verbose` flag:** Overrides the "not enough data" gate. Shows whatever partial data exists, clearly labeled as preliminary. Example: "1 session recorded (ratio 1.52x). Calibration not yet active."

### Recommendations — Scope and Rules

Recommendations must be:
- **Data-backed:** Every recommendation cites the specific data that triggered it
- **Plain language:** No raw parameter names exposed to the user. "Your reviews consistently take 3+ cycles but estimates assume 2" not "Increase review_cycles_default from 2 to 3"
- **Actionable:** Each recommendation has a concrete action the user can take via "Apply?"

**In-scope recommendation types:**

| Signal | Recommendation | Action |
|--------|---------------|--------|
| Actual review cycles consistently exceed review_cycles_default | "Your reviews consistently take N+ cycles but estimates assume {default}. Raise the default?" | Edit review_cycles_default in heuristics.md |
| Band multipliers too wide/narrow (e.g., >80% of actuals within Optimistic band) | "Your actuals consistently land below the Optimistic estimate. Your estimates may be too conservative." | Edit band multipliers in heuristics.md |
| Complexity systematically over/under-estimated for a project type | "Sessions tagged '{type}' consistently overrun by {X}x. Consider using higher complexity for these." | Workflow guidance (no file edit) |
| High outlier rate (>50% of sessions are outliers) | "More than half your sessions are flagged as outliers. Calibration may not be reliable. Consider resetting." | Reset calibration (destructive — two-step confirmation) |
| Specific session is an outlier due to obvious cause | "Session {timestamp} has ratio {ratio}x — likely caused by {reason}. Exclude from calibration?" | Exclude session (destructive — two-step confirmation) |
| A specific step consistently dominates cost | "Implementation accounts for {X}% of your spend. Consider whether file count or complexity settings reflect your actual workload." | Workflow guidance (no file edit) |
| Stale pricing data (>90 days old) | "Pricing data is {N} days old. Estimates may not reflect current API prices." | Workflow guidance (check pricing.md) |

**Out of scope for recommendations:**
- Raw parameter tuning (e.g., "change parallel_cache_rate_reduction from 0.15 to 0.12")
- Any recommendation that requires understanding the algorithm to evaluate
- Recommendations about parameters the user hasn't interacted with (e.g., don't suggest changing decay_halflife_days unless there's clear evidence it's wrong)

### The "Apply?" Interaction

**Non-destructive actions (heuristics.md edits):**
1. Show recommendation with supporting data
2. Prompt: "Apply? [y/N]"
3. If yes: edit heuristics.md, then show side-by-side accuracy diff
   - Example: "Before: 64% of sessions within Expected band / After: 78% of sessions within Expected band"
4. Continue to next recommendation

**Destructive actions (reset calibration, exclude session):**
1. Show recommendation with supporting data
2. Prompt: "Apply? [y/N]"
3. If yes: show warning explaining what will be deleted/changed
4. Second confirmation: "Are you sure? This will delete {description}. Proceed? [y/N]"
5. If confirmed: perform action, then show side-by-side accuracy diff
6. Continue to next recommendation

### Flags

| Flag | Behavior |
|------|----------|
| `--verbose` | Show partial data even when history is sparse (overrides "not enough data" gate) |
| `--json` | Output raw analysis as structured JSON. No markdown formatting. Skips interactive prompts. For programmatic/agent consumption. |
| `--no-apply` | Show recommendations but skip all "Apply?" prompts. Read-only mode. |

`--json` and `--no-apply` can be combined. `--json` implies `--no-apply`.

---

## Success Criteria (v2.0)

- SC-7: `/tokencostscope status` with 5+ clean sessions displays all 5 sections (health, accuracy, cost attribution, outliers, recommendations) with accurate data.
- SC-8: Recommendations are triggered only when data supports them (no false positives on < 3 data points).
- SC-9: "Apply?" on a heuristics.md edit modifies the correct parameter value and shows a before/after accuracy diff.
- SC-10: Destructive "Apply?" actions require two-step confirmation before executing.
- SC-11: `--json` output is valid JSON parseable by downstream agents/scripts.
- SC-12: `--no-apply` shows recommendations without prompting.
- SC-13: `--verbose` with 1 session shows partial data with appropriate "preliminary" labeling.
- SC-14: Adaptive time window correctly switches between "show all" (<=10 sessions) and rolling window (>10 sessions).
- SC-15: Same output format for ad-hoc and post-session invocations (no condensed mode).

---

## Constraints and Anti-Goals

### Constraints
- v2.0 status command depends on v1.7 per-agent step actuals being shipped first
- Python analysis script must work with `/usr/bin/python3` (system Python 3.9)
- All file paths must handle spaces (macOS volume path)
- Must not break existing calibration pipeline (learn.sh, update-factors.py, midcheck.sh)

### Anti-Goals (explicitly out of scope)
- **No raw parameter exposure:** Users never see internal parameter names like `parallel_cache_rate_reduction` in recommendations
- **No estimated cost attribution:** Per-step cost breakdown uses true actuals only (from v1.7), not estimated distribution
- **No condensed post-session mode:** Ad-hoc and post-session show identical output
- **No tool-level tracking in v1.7:** Agent-level granularity only; tool-level (individual reads/writes/searches within an agent) is deferred
- **No cross-project analysis:** Status is per-project only (cross-project is v3.0 roadmap)
- **No ML-based recommendations:** Rules-based only for v2.0

---

## Edge Cases and Error States

| Scenario | Behavior |
|----------|----------|
| No history.jsonl | "Not enough data yet" message (unless --verbose) |
| 1-2 records | "Not enough data yet, N more needed" (unless --verbose) |
| All records are outliers | "Not enough data yet, all sessions flagged as outliers" (unless --verbose) |
| No factors.json | Treat as uncalibrated; health summary shows "Calibration: not yet active" |
| history.jsonl exists but factors.json missing | Show history-based sections, note calibration not computed |
| Mixed v1.7+ and pre-v1.7 records | Cost attribution uses only sessions with step_actuals data; note "N sessions lack per-step data (pre-v1.7)" |
| Session with no Agent tool calls | No sidecar created; session has session-level cost only, no per-step breakdown |
| Nested/parallel agents | Each agent gets own span; no double-counting via JSONL byte offset attribution |
| heuristics.md has unexpected format after manual edit | Apply action fails gracefully with "Could not locate parameter in heuristics.md — please edit manually" |
| window= override with no sessions in range | "No sessions found in the specified window" |
| Concurrent session writing to history.jsonl during status read | Read snapshot at invocation time; do not lock |

---

## Deferred Decisions

- **Tool-level tracking within agents:** Deferred beyond v1.7. Sidecar format is extensible to support it later. Add to roadmap as a future version item.
- **Cross-project status rollup:** v3.0 roadmap item. Status is per-project only in v2.0.
- **Recommendation learning:** Status does not learn which recommendations the user accepts/rejects. This data could inform v5.0 automated orchestration but is not captured in v2.0.
- **review_cycles_actual auto-detection:** Currently null in history records. Could be derived from sidecar data in v1.7 (count Staff Review agent spans). Decision: include if feasible, defer if complex.
- **Accuracy diff calculation method:** The before/after diff shown after "Apply?" needs a concrete formula (e.g., % of sessions where actual falls within Expected band). Exact formula to be determined during architecture.

---

## Open Questions

1. **Sidecar file naming convention:** Should it be `{session-id}-timeline.jsonl` or a single `calibration/timeline.jsonl` appended per session? Naming affects cleanup and lookup.
2. **Byte offset vs. message index:** JSONL byte offsets are fragile if the file is modified. Would message sequence numbers (line numbers) be more robust for agent span attribution?
3. **review_cycles_actual from sidecar:** If v1.7 sidecar records Staff Review agent spans, should learn.sh auto-populate `review_cycles_actual` by counting those spans? This would unlock the review cycle recommendation without additional work.
4. **Recommendation priority/ordering:** When multiple recommendations fire, should they be ordered by impact (biggest accuracy improvement first) or by confidence (most data points first)?

---

## Scope Summary

### v1.7 — Per-Agent Step Actuals
- PreToolUse/PostToolUse hooks on Agent tool
- Sidecar timeline file (extensible JSON events, agent-level granularity)
- `sum-session-tokens.py` updates to attribute cost per agent span
- `history.jsonl` gains `step_actuals` field
- `update-factors.py` uses true per-step ratios instead of proportional attribution
- Backward compatible with pre-v1.7 sessions

### v2.0 — /tokencostscope status
- `scripts/tokencostscope-status.py` analysis script (reads history, factors, heuristics)
- SKILL.md integration for formatting and interactive Apply flow
- 5 output sections: health, accuracy trend, cost attribution, outlier report, recommendations
- Interactive "Apply? [y/N]" with two-step confirmation for destructive actions
- Side-by-side accuracy diff after applying recommendations
- Flags: `--verbose`, `--json`, `--no-apply`
- Adaptive time window with user override
- Sparse history graceful degradation with --verbose escape hatch
