# Architecture Decision: v1.7 Per-Agent Step Actuals + v2.0 /tokencostscope status

## Decision Summary

v1.7 introduces hook-based per-agent cost attribution via a sidecar timeline file. PreToolUse and PostToolUse hooks on the Agent tool record agent start/stop events with JSONL line numbers (not byte offsets). At session end, `sum-session-tokens.py` uses these line ranges to attribute actual token costs to individual agents. v2.0 builds on this data to provide a `/tokencostscope status` command backed by `tokencostscope-status.py`, which reads history, factors, and heuristics to produce a structured analysis with interactive recommendations.

The chosen approach uses **per-session sidecar files** named `{session-id}-timeline.jsonl`, **JSONL line indices** for span attribution, and **impact-then-confidence ordering** for recommendations.

---

## Open Question Resolutions

### Q1: Sidecar File Naming — Per-Session Files

**Decision:** Per-session files: `calibration/{session-id}-timeline.jsonl`

**Rationale:** A single appended `timeline.jsonl` creates three problems: (1) concurrent sessions (rare but possible with worktrees) would interleave events, (2) cleanup of old timeline data requires parsing and rewriting rather than simple file deletion, (3) session boundaries must be encoded as synthetic delimiter events. Per-session files keep each session's data self-contained.

Session ID discovery is straightforward: the JSONL file path under `~/.claude/projects/` already encodes the session ID in its filename. The PreToolUse/PostToolUse hook payloads include the `session_id` field. If absent, fall back to the PID-based approach (`$$` in bash), which is unique per session.

Cleanup: `learn.sh` deletes the sidecar after processing (same lifecycle as `active-estimate.json`). Crash recovery: orphaned sidecar files older than 7 days can be swept by `learn.sh` on next run.

### Q2: Byte Offset vs. Line Index — Line Index

**Decision:** Use JSONL line numbers (1-based line indices), not byte offsets.

**Rationale:** Byte offsets are theoretically stable for an append-only JSONL file during a single session. However, line indices are superior for three reasons:

1. **Robustness to encoding changes.** If a future Claude Code version normalizes line endings or re-encodes characters, byte offsets become invalid. Line indices survive any transformation that preserves line structure.
2. **Simpler attribution algorithm.** `sum-session-tokens.py` already iterates line-by-line. Attributing cost to a line range `[start_line, end_line)` requires only a line counter, no byte tracking.
3. **Debuggability.** `sed -n '42,67p' session.jsonl` is more intuitive than byte-offset slicing for inspecting attributed spans.

The hook records `jsonl_line_count` at fire time (the current line count of the session JSONL). This is obtained via `wc -l` (single syscall on the file, no full parse). The start event records the line count at agent dispatch; the stop event records the line count at agent return. Lines in `[start_line, end_line)` belong to that agent.

**Edge case — nested agents:** A parent agent's span is `[parent_start, parent_end)`. A child agent's span is `[child_start, child_end)` where `child_start >= parent_start` and `child_end <= parent_end`. The parent's attributed lines are `[parent_start, child_start) UNION [child_end, parent_end)` — child spans are subtracted to prevent double-counting.

**Edge case — parallel agents:** Parallel agents may produce interleaved JSONL lines. Each agent's lines are identified by the `[start, end)` ranges. If two agents have overlapping line ranges (which can happen with truly concurrent agents), lines in the overlap are attributed to the agent that started later (inner span wins), or split proportionally by token count. In practice, Claude Code's Agent tool is sequential at the orchestrator level — parallel agents are dispatched sequentially from the orchestrator's perspective, so their JSONL spans do not overlap.

### Q3: review_cycles_actual Auto-Population — Include in v1.7

**Decision:** Yes, include. The implementation cost is minimal once the sidecar exists.

**Rationale:** The sidecar records agent start/stop events by name. Counting events where `agent_name` matches the Staff Review step name (case-insensitive match on "staff" and "review") gives the actual review cycle count. This is a simple `len([e for e in events if is_staff_review(e.agent_name) and e.type == 'stop'])` computation in `learn.sh`'s RECORD Python block.

This unlocks the review cycle recommendation in v2.0 without additional work and fills the `review_cycles_actual: null` gap that has existed since v1.2.

### Q4: Recommendation Priority — Impact First, Confidence as Tiebreaker

**Decision:** Order recommendations by estimated accuracy impact (largest improvement first). When two recommendations have similar impact (within 5%), order by confidence (more data points first).

**Rationale:** Users want the highest-value action first. Impact ordering surfaces the recommendation most likely to meaningfully improve their estimates. Confidence-first ordering would surface "safe" recommendations that may have negligible effect. Recency-first was rejected because a signal that has persisted across many sessions is more actionable than a recent one-off.

---

## v1.7 Architecture

### Sidecar Event Schema (v1)

Each line is a JSON object. The schema is intentionally minimal and extensible.

```json
{
  "schema_version": 1,
  "type": "agent_start" | "agent_stop",
  "timestamp": "2026-03-22T14:30:00.123Z",
  "agent_name": "researcher",
  "session_id": "abc123",
  "jsonl_line_count": 42,
  "parent_agent": null | "team-lead",
  "metadata": {}
}
```

**Fields:**
- `schema_version` (int): Always `1` for this release. Future versions increment this. Readers must handle unknown versions gracefully (skip events with unknown schema_version).
- `type` (string): `"agent_start"` when the Agent tool is invoked, `"agent_stop"` when it returns. Future types (e.g., `"tool_start"`, `"tool_stop"` for tool-level granularity) can be added without breaking existing readers.
- `timestamp` (string): ISO 8601 with milliseconds. Wall-clock time, not monotonic — used only for human inspection and outlier debugging, not for attribution.
- `agent_name` (string): The `name` parameter passed to the Agent tool. Lowercased and trimmed at write time. This is the link to canonical step names in heuristics.md.
- `session_id` (string): Session identifier. Used to correlate sidecar with JSONL.
- `jsonl_line_count` (int): 1-based line count of the session JSONL at the moment this event fires. For `agent_start`, this is the line count before the agent runs. For `agent_stop`, this is the line count after the agent's final output is written.
- `parent_agent` (string|null): If this agent was spawned by another agent (nested), the parent's name. Null for top-level agents. Enables nested span subtraction.
- `metadata` (object): Extensible key-value pairs. Empty `{}` for v1.7. Reserved for future tool-level events (e.g., `tool_name`, `tool_input_hash`).

**Framework-agnostic note:** This schema does not reference Claude Code internals. Any agent framework that can emit JSONL events with these fields can feed into the attribution pipeline. The `jsonl_line_count` field assumes a line-oriented transcript log exists; frameworks using different log formats would need an adapter that maps their log positions to equivalent line indices.

### Hook Implementation

**New file:** `scripts/tokencostscope-agent-hook.sh`

This single script handles both PreToolUse and PostToolUse events on the Agent tool. The hook type is determined from the stdin JSON payload's `hookEventName` field.

**PreToolUse (Agent):** Fires before the Agent tool executes.
1. Read stdin JSON. Extract `tool_input.name` as agent_name, `session_id`.
2. Obtain `jsonl_line_count` via `wc -l < "$JSONL_PATH"` (fast — single syscall).
3. Determine `parent_agent` from environment or stdin context (Claude Code provides nesting context in the hook payload; if absent, null).
4. Append `agent_start` event to sidecar file.

**PostToolUse (Agent):** Fires after the Agent tool returns.
1. Read stdin JSON. Extract `tool_input.name` as agent_name, `session_id`.
2. Obtain `jsonl_line_count` via `wc -l < "$JSONL_PATH"`.
3. Append `agent_stop` event to sidecar file.

**Performance target:** < 5ms per hook invocation. The only I/O is `wc -l` (single syscall) and one append write. No JSONL parsing occurs in the hook.

**Existing hook coexistence:** The current `tokencostscope-track.sh` (PostToolUse on Agent) detects plans and nudges cost estimation. The new agent hook serves a completely different purpose (step timing). Both hooks will fire on PostToolUse/Agent. They must be registered as separate entries in the hooks array. `tokencostscope-track.sh` is unmodified.

**settings.json update:**
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          { "type": "command", "command": "bash '...tokencostscope-midcheck.sh'" }
        ]
      },
      {
        "matcher": "Agent",
        "hooks": [
          { "type": "command", "command": "bash '...tokencostscope-agent-hook.sh'" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Agent",
        "hooks": [
          { "type": "command", "command": "bash '...tokencostscope-track.sh'" },
          { "type": "command", "command": "bash '...tokencostscope-agent-hook.sh'" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "bash '...tokencostscope-learn.sh'" }
        ]
      }
    ]
  }
}
```

**Sidecar file naming:** `calibration/{session-id}-timeline.jsonl`. Session ID comes from the hook payload. If unavailable, falls back to a deterministic hash of the JSONL path (which is unique per session).

**Atomic append:** Each event is a single `echo "$JSON" >> "$SIDECAR_FILE"`. POSIX guarantees atomic append for writes under PIPE_BUF (4096 bytes on all platforms). Each event JSON is well under this limit (~300 bytes).

### Attribution Algorithm (in sum-session-tokens.py)

New function: `sum_session_by_agent(jsonl_path, sidecar_path, baseline_cost)`.

1. **Load sidecar events.** Parse `{session-id}-timeline.jsonl`. Build a list of spans: `[(agent_name, start_line, end_line, parent_agent)]`. Match `agent_start`/`agent_stop` pairs by agent_name using a stack (handles nesting).
2. **Handle nesting.** For each span with a `parent_agent`, subtract it from the parent's effective range. A parent's cost = sum of lines in its range minus all child ranges.
3. **Attribute lines.** Iterate the session JSONL line by line. For each assistant message with usage data, determine which span it falls in (binary search on sorted span start lines). Accumulate cost per agent.
4. **Unattributed lines.** Lines outside any span (orchestrator overhead, pre-agent setup) are grouped under `"_orchestrator"`.
5. **Return value:** `{"step_actuals": {"Research Agent": 2.31, "Implementation": 4.56, "_orchestrator": 0.89}, ...}` plus the existing session-level totals.

**Fallback:** If `sidecar_path` is None or the file doesn't exist, return session-level totals only (no `step_actuals`). This is the pre-v1.7 behavior.

### learn.sh Changes

1. **Discover sidecar.** After finding `LATEST_JSONL`, look for `calibration/*-timeline.jsonl` matching the session. Pattern: find the sidecar whose `session_id` events match, or use filename matching if session ID is in the JSONL filename.
2. **Pass sidecar to sum-session-tokens.py.** New optional third positional arg: `python3 sum-session-tokens.py "$LATEST_JSONL" "$BASELINE_COST" "$SIDECAR_PATH"`.
3. **Extract step_actuals from result.** The ACTUAL_JSON now includes `step_actuals` dict.
4. **Compute true per-step ratios.** In the RECORD Python block, if `step_actuals` is present AND `step_costs_estimated` is present:
   ```python
   step_ratios = {}
   for step_name, estimated in step_costs_estimated.items():
       actual = step_actuals.get(step_name, 0)
       if estimated > 0 and actual > 0:
           step_ratios[step_name] = round(actual / estimated, 4)
   ```
   If `step_actuals` is absent, fall back to proportional attribution (current behavior).
5. **Write `step_actuals` to history record.** New field in the JSON record.
6. **Auto-populate `review_cycles_actual`.** Count `agent_stop` events where agent_name matches staff review pattern:
   ```python
   review_cycles_actual = len([e for e in sidecar_events
       if e['type'] == 'agent_stop'
       and 'staff' in e['agent_name'].lower()
       and 'review' in e['agent_name'].lower()])
   ```
7. **Cleanup sidecar.** After successful processing: `rm -f "$SIDECAR_PATH"`.
8. **Sweep orphans.** Before processing, delete any sidecar files in `calibration/` older than 7 days:
   ```bash
   find "$CALIBRATION_DIR" -name "*-timeline.jsonl" -mtime +7 -delete 2>/dev/null || true
   ```

### update-factors.py Changes

**Pass 4 update:** When a history record has a `step_actuals` field (non-null), the `step_ratios` in that record contain true per-step ratios (actual_step / estimated_step). The existing Pass 4 code already reads `step_ratios` and computes per-step factors — no algorithm change is needed. The improvement is in the quality of input data: true ratios replace the proportional attribution proxy.

**Backward compatibility:** Records without `step_actuals` (pre-v1.7) continue to use their proportional-attribution `step_ratios`. Both old and new records feed into the same aggregation. As v1.7+ records accumulate, the per-step factors will naturally converge toward true per-step accuracy.

### Agent Name to Step Name Mapping

The `agent_name` from the hook (e.g., `"researcher"`, `"implementer"`) must map to canonical step names in heuristics.md (e.g., `"Research Agent"`, `"Implementation"`). This mapping is needed in two places: `learn.sh` (matching step_actuals keys to step_costs_estimated keys) and `tokencostscope-status.py` (grouping for display).

**Approach:** A mapping table in `sum-session-tokens.py`:

```python
AGENT_TO_STEP = {
    "researcher": "Research Agent",
    "research": "Research Agent",
    "architect": "Architect Agent",
    "engineer": "Engineer Initial Plan",  # ambiguous — see below
    "staff-reviewer": "Staff Review",
    "staff_reviewer": "Staff Review",
    "implementer": "Implementation",
    "implement": "Implementation",
    "qa": "QA",
    "frontend-designer": "Frontend Designer",
    "docs-updater": "Docs Updater",
}
```

**Ambiguity:** `"engineer"` maps to either "Engineer Initial Plan" or "Engineer Final Plan". Resolution: use ordinal position — first engineer agent in the session maps to Initial, second to Final. This is tracked via a counter during span construction.

**Unrecognized agents:** Agents not in the mapping are stored under their raw name. The status command groups them under "Other."

---

## v2.0 Architecture

### tokencostscope-status.py

**Location:** `scripts/tokencostscope-status.py`
**Invocation:** `python3 scripts/tokencostscope-status.py [options]`
**Runtime:** `/usr/bin/python3` (system Python 3.9, stdlib only)

**Arguments:**
```
--history PATH       Path to history.jsonl (default: calibration/history.jsonl)
--factors PATH       Path to factors.json (default: calibration/factors.json)
--heuristics PATH    Path to heuristics.md (default: references/heuristics.md)
--window SPEC        Time window: "30d" (days) or "10" (count) or "all"
--verbose            Show partial data when history is sparse
--json               Output structured JSON, skip interactive prompts
--no-apply           Show recommendations but skip Apply prompts
```

**Output:** JSON to stdout. SKILL.md reads this JSON and formats it as markdown for the user.

### JSON Output Schema (v1)

```json
{
  "schema_version": 1,
  "generated_at": "2026-03-22T14:30:00Z",
  "window": {
    "type": "adaptive" | "days" | "count" | "all",
    "value": 30,
    "sessions_in_window": 12,
    "sessions_total": 15
  },
  "health": {
    "status": "active" | "collecting" | "no_data",
    "clean_samples": 10,
    "total_records": 12,
    "outlier_count": 2,
    "active_factor_level": "per-step" | "per-signature" | "size-class" | "global" | "none",
    "factor_value": 1.12,
    "message": "Calibration: active, 10 clean samples, per-step factors for 3 steps"
  },
  "accuracy": {
    "trend": "improving" | "stable" | "degrading" | "insufficient_data",
    "mean_ratio": 1.15,
    "median_ratio": 1.08,
    "pct_within_expected": 0.64,
    "pct_within_pessimistic": 0.92,
    "sessions": [
      {
        "timestamp": "2026-03-20T...",
        "size": "M",
        "ratio": 1.07,
        "expected": 6.24,
        "actual": 6.68,
        "band_hit": "expected"
      }
    ]
  },
  "cost_attribution": {
    "has_step_data": true,
    "sessions_with_step_data": 8,
    "sessions_without_step_data": 4,
    "steps": [
      {
        "name": "Implementation",
        "actual_total": 34.56,
        "pct_of_total": 0.42,
        "estimated_total": 30.00,
        "accuracy_ratio": 1.15
      }
    ],
    "note": null | "4 sessions lack per-step data (pre-v1.7)"
  },
  "outliers": {
    "count": 2,
    "rate": 0.15,
    "sessions": [
      {
        "timestamp": "2026-03-01T...",
        "size": "S",
        "ratio": 4.5,
        "expected": 1.0,
        "actual": 4.5,
        "probable_cause": null | "All work ran on Opus instead of estimated Sonnet"
      }
    ],
    "patterns": []
  },
  "recommendations": [
    {
      "id": "review_cycles_high",
      "priority": 1,
      "description": "Your reviews consistently take 3+ cycles but estimates assume 2. Raise the default?",
      "supporting_data": {
        "avg_actual_cycles": 3.2,
        "current_default": 2,
        "sessions_examined": 5
      },
      "action": {
        "type": "edit_heuristic",
        "file": "references/heuristics.md",
        "parameter": "review_cycles_default",
        "current_value": 2,
        "proposed_value": 3
      },
      "destructive": false,
      "impact_estimate": "expected_band_accuracy: 64% -> 78%"
    }
  ],
  "flags": {
    "verbose": false,
    "pricing_stale": false,
    "pricing_age_days": 18
  }
}
```

**Schema versioning:** The `schema_version` field enables backward-compatible evolution. Consumers should check `schema_version` and handle unknown fields gracefully. Breaking changes increment the version.

**Framework-agnostic note:** This JSON schema is the stable contract. Any system that can produce `history.jsonl` records (regardless of how they were generated) can feed `tokencostscope-status.py`. The `--json` output can be consumed by non-Claude-Code tooling.

### Recommendation Engine

Each recommendation type is a function that receives the windowed session data and returns `None` (not triggered) or a recommendation dict.

**Rules and thresholds:**

| ID | Signal | Threshold | Action Type |
|----|--------|-----------|-------------|
| `review_cycles_high` | Mean `review_cycles_actual` > `review_cycles_default` + 0.5 | >= 3 sessions with `review_cycles_actual` | `edit_heuristic` |
| `bands_too_wide` | > 80% of actuals within Optimistic band | >= 5 sessions | Guidance |
| `bands_too_narrow` | > 30% of actuals exceed Pessimistic band | >= 5 sessions | Guidance |
| `high_outlier_rate` | > 50% of all records are outliers | >= 6 total records | `reset_calibration` (destructive) |
| `step_dominance` | One step accounts for > 60% of total spend | >= 3 sessions with step_actuals | Guidance |
| `stale_pricing` | Pricing data > 90 days old | Always checked | Guidance |
| `session_outlier` | Individual session with ratio > 3.0 or < 0.2 | Per session | `exclude_session` (destructive) |

**Minimum data guards:** Each recommendation specifies its minimum session count. Below that threshold, the recommendation is not emitted regardless of signal strength. This satisfies SC-8 (no false positives on < 3 data points).

**Impact estimation:** For `edit_heuristic` actions, the script re-runs the estimate formula with the proposed parameter change against historical data to compute before/after accuracy percentages. This is the "side-by-side accuracy diff" from the requirements.

**Ordering:** Recommendations are sorted by `priority` (lower = higher priority). Priority is assigned by impact bucket: accuracy-affecting edits (1-3), workflow guidance (4-6), informational (7-9). Within the same bucket, more data points = lower priority number.

### SKILL.md Integration

**New invocation mode:** When the user types `/tokencostscope status [flags]`, SKILL.md detects the `status` keyword and branches into the status flow instead of the estimation flow.

**Status flow in SKILL.md:**

1. Parse flags from the invocation (`--verbose`, `--json`, `--no-apply`, `window=`).
2. Run `python3 scripts/tokencostscope-status.py [flags]`. Capture JSON output.
3. If `--json` flag: output raw JSON and stop.
4. Otherwise: format JSON into the markdown dashboard (5 sections as specified in requirements).
5. For each recommendation:
   a. Display description and supporting data.
   b. If `--no-apply`: show but skip prompt.
   c. If destructive: show warning, prompt "Apply? [y/N]", if yes show second confirmation "Are you sure? This will {description}. Proceed? [y/N]".
   d. If non-destructive: prompt "Apply? [y/N]".
   e. If approved: execute the action (edit heuristics.md, exclude session, reset calibration).
   f. After applying: re-run `tokencostscope-status.py` with `--json` to get updated accuracy metrics. Display before/after diff.

**The action execution happens in SKILL.md, not in the Python script.** The Python script computes what should change; SKILL.md (running as Claude with file editing capabilities) applies the change. This keeps the Python script pure computation with no side effects.

### Apply Actions — Implementation Details

**edit_heuristic:** The recommendation JSON includes `file`, `parameter`, `current_value`, and `proposed_value`. SKILL.md reads heuristics.md, finds the line containing `parameter = current_value` (or the equivalent markdown table cell), and edits it to `proposed_value`. If the parameter cannot be located (user manually reformatted the file), SKILL.md responds: "Could not locate parameter in heuristics.md -- please edit manually: change {parameter} from {current_value} to {proposed_value}."

**exclude_session:** The recommendation includes the session's timestamp and position in history.jsonl. SKILL.md adds an `"excluded": true` field to that record in history.jsonl (edit in place). `update-factors.py` is updated to skip records with `"excluded": true` during factor computation (same treatment as outliers, but explicitly user-requested).

**reset_calibration:** Destructive. SKILL.md runs `rm calibration/history.jsonl calibration/factors.json` and reports completion. The sidecar files are left alone (they belong to the current session, not to history).

---

## Design Details

### Data Model Changes

**history.jsonl — new fields in v1.7:**
```json
{
  "step_actuals": {"Research Agent": 2.31, "Implementation": 4.56},  // null if no sidecar
  "review_cycles_actual": 3,  // int if sidecar has staff review spans, else null
  "attribution_method": "sidecar" | "proportional"  // how step_ratios were computed
}
```

All new fields use `.get()` defaults. `step_actuals` defaults to `null`. `review_cycles_actual` defaults to `null` (unchanged field, just now populated). `attribution_method` defaults to `"proportional"` for pre-v1.7 records.

**history.jsonl — new field in v2.0:**
```json
{
  "excluded": false  // set to true by status command's exclude_session action
}
```

Defaults to `false` via `.get('excluded', False)`.

**factors.json — no schema changes.** The existing schema supports all v1.7/v2.0 needs. Per-step factors will simply become more accurate as true ratios replace proportional attribution.

**active-estimate.json — no schema changes.** The sidecar file is a separate file, not embedded in the estimate.

### Component Structure

```
scripts/
  tokencostscope-agent-hook.sh    # NEW — PreToolUse + PostToolUse hook for Agent tool
  tokencostscope-status.py        # NEW — status analysis engine
  tokencostscope-learn.sh         # MODIFIED — sidecar discovery, true step ratios
  sum-session-tokens.py           # MODIFIED — per-agent attribution function
  update-factors.py               # MODIFIED — skip excluded records
  tokencostscope-midcheck.sh      # UNCHANGED
  tokencostscope-track.sh         # UNCHANGED

calibration/
  active-estimate.json            # UNCHANGED schema
  history.jsonl                   # NEW fields: step_actuals, attribution_method
  factors.json                    # UNCHANGED schema
  {session-id}-timeline.jsonl     # NEW — per-session sidecar files (transient)
  .midcheck-state                 # UNCHANGED

SKILL.md                          # MODIFIED — status invocation mode, version bump
references/heuristics.md          # UNCHANGED in v1.7; editable by v2.0 Apply actions
```

### Integration Points

1. **Hook payload → agent-hook.sh:** The Claude Code hook system passes a JSON payload on stdin with `hookEventName`, `tool_input` (containing agent `name`), and `session_id`. The hook reads these fields.

2. **agent-hook.sh → sidecar file:** Append-only JSONL writes. One event per hook invocation.

3. **learn.sh → sum-session-tokens.py:** Existing interface extended with optional sidecar path argument. Return value gains `step_actuals` field.

4. **sum-session-tokens.py → sidecar file:** Read-only. Loads spans, attributes JSONL lines to agents.

5. **learn.sh → history.jsonl:** Existing append interface. Record gains new fields.

6. **update-factors.py → history.jsonl:** Existing read interface. Now also checks `excluded` field.

7. **SKILL.md → tokencostscope-status.py:** New interface. SKILL.md invokes with flags, reads JSON stdout.

8. **SKILL.md → heuristics.md (Apply action):** Existing file, new edit pattern for recommendation application.

---

## Rejected Alternatives

### Alt 1: Single Appended timeline.jsonl (for sidecar naming)

**What it was:** A single `calibration/timeline.jsonl` file that accumulates events across all sessions, with session delimiters.

**Why rejected:** Concurrent session safety requires file locking or per-line session tagging. Cleanup of old events requires parsing the entire file to find session boundaries. Deletion of a single session's events requires rewriting the file. Per-session files avoid all three problems with no downside — the only cost is session ID discovery, which is already available from hook payloads.

### Alt 2: Byte Offsets for Span Attribution

**What it was:** Record the byte offset into the session JSONL at agent start/stop, then use byte-range slicing to attribute cost.

**Why rejected:** Byte offsets are fragile if the JSONL file undergoes any transformation (encoding normalization, line ending changes). They also require tracking byte positions in the Python parser (less natural than line counting). The performance difference is negligible — both approaches are O(n) in JSONL size. Line indices are more robust, more debuggable, and simpler to implement.

### Alt 3: Embedding Timeline Data in active-estimate.json

**What it was:** Instead of a separate sidecar file, append agent span data to `active-estimate.json` as it accumulates during the session.

**Why rejected:** `active-estimate.json` is a snapshot written once at estimate time. Making it append-only during the session would break its current semantics and require `learn.sh` to handle a mixed-format file. The sidecar separation keeps concerns clean: estimate data is immutable after write, timeline data grows during the session.

### Alt 4: Python-based Hook Instead of Shell

**What it was:** Write `tokencostscope-agent-hook.py` in Python for cleaner JSON handling.

**Why rejected:** Claude Code hooks invoke commands via shell. A Python hook adds ~100ms startup overhead (Python interpreter init) vs ~5ms for bash. Since this hook fires on every Agent tool call, the 20x latency difference matters. The bash script is minimal (read stdin JSON via a small python3 -c inline, append one line). Shell is the right tool for this hot path.

### Alt 5: Status Command as Standalone Shell Script

**What it was:** Implement the status command as a bash script similar to learn.sh and midcheck.sh.

**Why rejected:** The status command performs complex data analysis (trend computation, recommendation rules, impact estimation). This is Python's strength, not bash's. The existing learn.sh and midcheck.sh are bash because they are hot-path hooks where startup time matters. The status command is user-invoked and runs once — Python startup time is irrelevant.

### Alt 6: Recommendation Learning (tracking Accept/Reject)

**What it was:** Track which recommendations users accept or reject to learn their preferences over time.

**Why rejected by requirements:** Explicitly deferred to v5.0 (per requirements doc). Including it in v2.0 would add schema complexity (a new `recommendation_history.jsonl` file) and UI complexity (displaying historical acceptance rates) without clear v2.0 value.

---

## Risks and Mitigations

### Risk 1: Hook Payload Changes

**Risk:** Claude Code's hook payload format may change between versions, breaking field extraction in `tokencostscope-agent-hook.sh`.

**Mitigation:** The hook script uses `.get()` with defaults for all fields. If `session_id` is absent, fall back to JSONL-path hash. If `tool_input.name` is absent, skip the event (fail-silent, same pattern as midcheck.sh). Pin the expected payload fields in a comment block at the top of the script for easy identification when updating.

### Risk 2: Line Count Accuracy

**Risk:** `wc -l` in the hook may race with Claude Code writing to the JSONL. The line count could be slightly off if a write is in progress.

**Mitigation:** This is benign — off-by-one line attribution means a single assistant message (~$0.01-0.05) is attributed to the wrong agent. The aggregate per-step cost remains accurate to within 1-2%. This is far better than the current proportional attribution (which has no per-step signal at all). No mitigation needed.

### Risk 3: Sidecar File Accumulation

**Risk:** If `learn.sh` fails or is not configured, sidecar files accumulate indefinitely in `calibration/`.

**Mitigation:** The 7-day sweep in `learn.sh` catches orphans. Additionally, sidecar files are tiny (~1KB each). Even 100 orphaned files use < 100KB. The risk is cosmetic, not operational.

### Risk 4: Agent Name Mapping Drift

**Risk:** Users may use non-standard agent names (e.g., `"impl-1"` instead of `"implementer"`) that don't match the mapping table.

**Mitigation:** Unrecognized agents are stored under their raw name and grouped under "Other" in the status command. The mapping table covers the standard names from CLAUDE.md's agent delegation table. Users with custom agent names will see partial attribution (mapped agents get per-step data, unmapped agents get lumped into "Other"). This degrades gracefully per SC-5.

### Risk 5: Large History Files in Status Command

**Risk:** After many months, `history.jsonl` could have hundreds of records. The status command reads all of them.

**Mitigation:** The adaptive window limits analysis to 10-30 recent sessions. However, the script still reads the full file to compute totals and outlier rates. At 1KB per record, even 1000 records is 1MB — trivial for Python to parse. No mitigation needed for foreseeable scale.

### Risk 6: Heuristics.md Format Changes Break Apply

**Risk:** If the user manually reformats `heuristics.md`, the Apply action's pattern matching may fail to locate the parameter.

**Mitigation:** The Apply action in SKILL.md searches for the parameter name and current value. If not found, it reports the failure gracefully and tells the user to edit manually. This is specified in the requirements (edge case table, row "heuristics.md has unexpected format after manual edit").

---

## Open Questions (Requiring Human Judgment)

### OQ-1: PreToolUse vs. PostToolUse Hook Registration

The current `settings.json` uses `"matcher": "Agent"` on PostToolUse. For the new agent hook, we need BOTH PreToolUse (agent_start) and PostToolUse (agent_stop) to fire on Agent tool calls.

**Question for user:** Claude Code's hook system — does PreToolUse support the `"matcher"` field to filter by tool name? The existing midcheck.sh PreToolUse hook has no matcher (fires on all tools). If `"matcher": "Agent"` is not supported on PreToolUse, the agent-hook.sh script must self-filter: check the tool name from stdin JSON and exit early for non-Agent tools.

**Recommendation:** Implement the self-filtering approach regardless, as it is defensive and works regardless of matcher support. The performance cost of reading stdin JSON and checking one field is negligible (<1ms).

### OQ-2: Session ID Availability in Hook Payload

**Question for user:** Does the Claude Code hook stdin payload include a `session_id` field? The midcheck.sh script does not use it. If absent, the sidecar file naming falls back to a hash of the JSONL path, which is deterministic but less debuggable.

**Recommendation:** Proceed with the hash fallback as the primary approach. If session_id becomes available, it's a straightforward improvement that doesn't change the architecture.

### OQ-3: Maximum Sidecar Events Per Session

In a large session with many agent calls (e.g., an L-size change with 10+ agents in parallel teams), the sidecar could accumulate 50+ events. This is not a concern (50 events * 300 bytes = 15KB), but flagging in case there's a policy on per-session file growth.

**Recommendation:** No limit needed. 50-100 events is negligible. Document the expected range (10-100 events per session) in the sidecar schema docs.

---

## Framework-Agnostic Adaptation Points

The design identifies three interfaces where a non-Claude-Code agent framework would need adapters:

1. **Event emission (sidecar writes):** Currently implemented as bash hooks that fire on Claude Code's PreToolUse/PostToolUse events. A different framework would need to emit the same sidecar JSONL events at equivalent points in its agent lifecycle. The sidecar schema (schema_version=1) is the stable contract — any system that produces conformant events can feed into the attribution pipeline.

2. **Transcript log (JSONL):** The attribution algorithm assumes a line-oriented JSONL transcript where each assistant message has `usage` data with token counts. Frameworks with different log formats need an adapter that either (a) converts their log to the expected JSONL format, or (b) provides an alternative `sum_session_by_agent()` implementation. The sidecar's `jsonl_line_count` field would map to whatever line/offset scheme the alternative log uses.

3. **Status output consumption (`--json`):** The JSON output schema is framework-agnostic. Any system can consume it. The interactive Apply flow (editing heuristics.md) is Claude Code-specific (relies on SKILL.md's file editing capability), but the recommendations themselves are framework-neutral data.

**No adapter code is included in v1.7/v2.0.** These notes document where the seams are for future extension without over-engineering the current implementation.

---

## Version Bumps

- **v1.7.0:** SKILL.md frontmatter, output template header, learn.sh VERSION, CLAUDE.md
- **v2.0.0:** Same four locations. v2.0 ships after v1.7 is merged and stable.

## Implementation Ordering

v1.7 and v2.0 are sequential — v2.0 depends on v1.7 data. Within v1.7:

1. `tokencostscope-agent-hook.sh` (new file, no dependencies)
2. `sum-session-tokens.py` changes (new `sum_session_by_agent` function)
3. `learn.sh` changes (sidecar discovery, true step ratios, review_cycles_actual)
4. `update-factors.py` changes (excluded field handling)
5. `settings.json` hook registration
6. Tests for all of the above

Within v2.0:

1. `tokencostscope-status.py` (new file, reads existing data)
2. SKILL.md status invocation mode
3. Tests for status analysis and recommendations
