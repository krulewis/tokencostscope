# Implementation Plan: v1.7 Per-Agent Step Actuals + v2.0 /tokencast status

## Overview

v1.7 adds hook-based per-agent cost attribution via a sidecar timeline file, enabling true per-step actual/expected cost ratios. v2.0 builds a `/tokencast status` dashboard on top of v1.7 data. The two versions ship sequentially: v1.7 is implemented and merged first; v2.0 follows.

Within v1.7, three independent changes (agent-hook.sh, sum-session-tokens.py additions, update-factors.py exclusion support) can be implemented in parallel. learn.sh depends on the first two. settings.json depends on agent-hook.sh. Tests can be written in parallel once interfaces are defined.

---

## v1.7 Changes

### Change 1: scripts/tokencast-agent-hook.sh

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-agent-hook.sh
Lines: new file
Parallelism: independent
Description: New bash hook script handling both PreToolUse and PostToolUse events on the Agent tool. Writes agent_start / agent_stop events to a per-session sidecar timeline JSONL. Fail-silent via || exit 0.
Details:
  - Shebang: #!/usr/bin/env bash
  - set -euo pipefail at top, followed by || exit 0 around entire body so hook never blocks tooling
  - Read stdin JSON payload via python3 -c inline (same pattern as midcheck.sh) — extract:
      hookEventName (string: "PreToolUse" or "PostToolUse")
      tool_input.name → agent_name (lowercase + strip)
      session_id (string; fallback: empty string)
      tool_name (string; used for early exit if not "Agent")
  - If tool_name != "Agent": exit 0 (self-filtering; works regardless of whether PreToolUse
    matcher field is supported by Claude Code hook system)
  - SCRIPT_DIR: derive from $0 using cd "$(dirname "$0")" && pwd
  - SKILL_DIR: dirname of SCRIPT_DIR
  - CALIBRATION_DIR: $SKILL_DIR/calibration
  - SESSION_ID: from payload; if empty, compute deterministic fallback:
      JSONL_PATH=$(find "$HOME/.claude/projects/" -name "*.jsonl" -newer "$CALIBRATION_DIR" -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -1)
      SESSION_ID=$(echo "$JSONL_PATH" | md5 | cut -c1-12)
      (macOS md5 command; if absent fall back to sha256sum | cut -c1-12)
  - SIDECAR_FILE: "$CALIBRATION_DIR/${SESSION_ID}-timeline.jsonl"
  - JSONL_PATH discovery: same find pattern as learn.sh, used to run wc -l
  - JSONL_LINE_COUNT: $(wc -l < "$JSONL_PATH" 2>/dev/null || echo 0)
  - TIMESTAMP: $(date -u +"%Y-%m-%dT%H:%M:%S.000Z")
  - EVENT_TYPE: "agent_start" if hookEventName == "PreToolUse", else "agent_stop"
  - PARENT_AGENT: extract tool_input.parent_agent from stdin if present; default null
  - Build EVENT_JSON via python3 -c inline using env vars (shlex-safe pattern):
      import json, os
      print(json.dumps({
          "schema_version": 1,
          "type": os.environ["TYPE"],
          "timestamp": os.environ["TS"],
          "agent_name": os.environ["AGENT"],
          "session_id": os.environ["SID"],
          "jsonl_line_count": int(os.environ["LC"]),
          "parent_agent": json.loads(os.environ["PA"]) if os.environ["PA"] != "null" else None,
          "metadata": {}
      }))
  - Atomic append: echo "$EVENT_JSON" >> "$SIDECAR_FILE"
    POSIX guarantees atomic appends < PIPE_BUF (~4096 bytes). Each event ~300 bytes.
  - mkdir -p "$CALIBRATION_DIR" before first write
  - Entire body wrapped: { ... } || exit 0 so any failure is silent
  - Comment block at top documenting expected payload fields (for forward-compatibility checks)
```

### Change 2: scripts/sum-session-tokens.py — new sum_session_by_agent function

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/sum-session-tokens.py
Lines: new function after existing sum_session(), ~70–90 new lines; main() updated (~10 lines)
Parallelism: independent
Description: Add sum_session_by_agent() function implementing the attribution algorithm.
  Extend main() to accept optional third positional arg (sidecar_path); when present,
  call sum_session_by_agent() and merge step_actuals into output dict.
Details:
  - AGENT_TO_STEP mapping dict (module-level constant):
      {
          "researcher": "Research Agent",
          "research": "Research Agent",
          "architect": "Architect Agent",
          "engineer": "Engineer Initial Plan",  # ordinal disambiguation in sum_session_by_agent
          "staff-reviewer": "Staff Review",
          "staff_reviewer": "Staff Review",
          "implementer": "Implementation",
          "implement": "Implementation",
          "qa": "QA",
          "frontend-designer": "Frontend Designer",
          "frontend_designer": "Frontend Designer",
          "docs-updater": "Docs Updater",
          "docs_updater": "Docs Updater",
      }
  - Function signature: sum_session_by_agent(jsonl_path: str, sidecar_path: str, baseline_cost: float = 0.0) -> dict
  - Step 1 — Load sidecar events:
      Parse sidecar JSONL line by line (skip lines with json.JSONDecodeError or schema_version != 1 or unknown types)
      Separate agent_start and agent_stop events
  - Step 2 — Build spans using a stack (handles nesting):
      For each agent_start event, push {agent_name, start_line, parent_agent} to a per-agent stack (dict keyed by agent_name)
      For each agent_stop event, pop from the stack and create span: (agent_name, start_line, end_line, parent_agent)
      Unmatched stops are discarded; unmatched starts at EOF get end_line = total_jsonl_lines
      "engineer" ordinal disambiguation: first engineer span → "Engineer Initial Plan", second → "Engineer Final Plan"
      Map agent_name via AGENT_TO_STEP; unmapped names used as-is
  - Step 3 — Nested span subtraction:
      For each span with parent_agent set, subtract child line range from parent effective ranges
      Parent effective lines = [start, end) MINUS all [child_start, child_end) ranges where child.parent == parent
      Data structure: dict of {step_name: list of (start, end) ranges} after subtraction
  - Step 4 — Attribute JSONL lines:
      Build sorted list of all span ranges (binary search by line number)
      Re-call sum_session() logic but per-span: iterate jsonl line by line with line counter,
      for each assistant message with usage, determine which span it falls in, accumulate cost
      Lines outside any span → "_orchestrator" bucket
      sum_session_by_agent() calls sum_session() first to get session totals, then does per-agent attribution
  - Step 5 — Return:
      Merge sum_session() result with {"step_actuals": {step_name: float, ...}}
      step_actuals values rounded to 4 decimal places
      "_orchestrator" key included in step_actuals if non-zero
      Return full dict
  - Fallback (sidecar_path None or file missing): return sum_session() result only (no step_actuals key)
  - main() update:
      Accept optional sys.argv[3] as sidecar_path (None if absent)
      If sidecar_path provided and file exists, call sum_session_by_agent() instead of sum_session()
      Otherwise call sum_session() as before
      This is backward compatible (existing two-arg invocation unchanged)
```

### Change 3: scripts/update-factors.py — excluded field handling

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/update-factors.py
Lines: Pass 1 (read loop), approximately lines 119–152
Parallelism: independent
Description: Skip records with excluded=true during factor computation. No other logic changes.
Details:
  - In Pass 1 read loop, after parsing each JSON record, before appending to all_records:
      if record.get('excluded', False):
          continue
  - This applies the same treatment as outliers (skip from factor computation) but for
    user-explicitly-excluded sessions (set by status command's exclude_session action)
  - Do NOT emit a stderr message for excluded records (unlike outliers which print "Outlier excluded:")
    User-excluded records are intentional; no diagnostic output needed
  - The excluded record still exists in history.jsonl (not deleted) — this is correct:
    history.jsonl is append-only; exclusion is soft via field
  - No changes to Passes 2, 3, 4, or 5 — only Pass 1 read loop is modified
  - All existing tests must continue to pass (excluded field defaults to False via .get())
```

### Change 4: scripts/tokencast-learn.sh — sidecar discovery, true step ratios, review_cycles_actual

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-learn.sh
Lines: multiple sections — VERSION bump (line 13), ACTUAL_JSON computation (~line 83),
  eval of ACTUAL_JSON (~lines 88–96), RECORD Python block (~lines 102–160), cleanup (~lines 162–171)
Parallelism: depends-on: [Change 1 (agent-hook.sh), Change 2 (sum-session-tokens.py)]
Description: Sidecar discovery, pass sidecar to sum-session-tokens.py, extract step_actuals,
  compute true per-step ratios when sidecar present, auto-populate review_cycles_actual,
  sweep orphaned sidecars, delete sidecar after processing.
Details:
  - VERSION: "1.6.0" → "1.7.0" (line 13)
  - After LATEST_JSONL is resolved (~line 74), add sidecar discovery block:
      SIDECAR_PATH=""
      # Sweep orphaned sidecar files older than 7 days before processing
      find "$CALIBRATION_DIR" -name "*-timeline.jsonl" -mtime +7 -delete 2>/dev/null || true
      # Find sidecar matching this session by session_id embedded in filename or content
      # Primary: match by JSONL path hash (same derivation as agent-hook.sh fallback)
      SESSION_ID_HASH=$(echo "$LATEST_JSONL" | md5 | cut -c1-12 2>/dev/null || echo "")
      CANDIDATE="$CALIBRATION_DIR/${SESSION_ID_HASH}-timeline.jsonl"
      if [ -f "$CANDIDATE" ]; then
          SIDECAR_PATH="$CANDIDATE"
      else
          # Secondary: newest *-timeline.jsonl newer than estimate (might be session-id based)
          SIDECAR_PATH=$(find "$CALIBRATION_DIR" -name "*-timeline.jsonl" -newer "$ESTIMATE_FILE" \
              -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -1 || echo "")
      fi
  - ACTUAL_JSON computation: extend python3 call to pass sidecar_path as third arg:
      ACTUAL_JSON=$(python3 "$SCRIPT_DIR/sum-session-tokens.py" "$LATEST_JSONL" "$BASELINE_COST" \
          "${SIDECAR_PATH:-}" 2>/dev/null) || { rm -f "$ESTIMATE_FILE"; exit 0; }
      Note: if SIDECAR_PATH is empty string, sum-session-tokens.py receives "" as argv[3];
      main() must treat "" as absent (len check: only use sidecar if path non-empty and exists)
  - Extend eval of ACTUAL_JSON to also extract step_actuals:
      eval "$(echo "$ACTUAL_JSON" | python3 -c "
      import sys, json
      d = json.load(sys.stdin)
      print(f'ACTUAL_COST={d.get(\"actual_cost\", 0)}')
      print(f'TURN_COUNT={d.get(\"turn_count\", 0)}')
      import shlex
      print(f'STEP_ACTUALS_JSON={shlex.quote(json.dumps(d.get(\"step_actuals\") or {}))}')
      ")" || { rm -f "$ESTIMATE_FILE"; exit 0; }
  - RECORD Python block — update comment and logic for true per-step ratios:
      Pass STEP_ACTUALS_JSON via env var SA_ENV
      In the Python block:
          step_actuals = json.loads(os.environ.get('SA_ENV', '{}')) or {}
          attribution_method = 'sidecar' if step_actuals else 'proportional'

          # True per-step ratios when sidecar data is present
          if step_actuals and step_costs_estimated:
              step_ratios = {}
              for step_name, estimated in step_costs_estimated.items():
                  actual = step_actuals.get(step_name, 0)
                  if estimated > 0 and actual > 0:
                      step_ratios[step_name] = round(actual / estimated, 4)
          else:
              # Proportional fallback: session_ratio applied to all steps (pre-v1.7 behavior)
              session_ratio = round(actual / expected, 4)
              step_ratios = {step: session_ratio for step in step_costs_estimated}

          # Auto-populate review_cycles_actual from sidecar events
          review_cycles_actual = None
          if step_actuals:
              # Load sidecar events from EST_FILE path (sidecar path is stored in env)
              # Actually, sidecar events are parsed from SIDECAR_PATH; pass via env var
              import json as _json
              sidecar_path = os.environ.get('SIDECAR_PATH_ENV', '')
              if sidecar_path and os.path.exists(sidecar_path):
                  events = []
                  with open(sidecar_path) as sf:
                      for line in sf:
                          try: events.append(_json.loads(line))
                          except: pass
                  review_cycles_actual = len([e for e in events
                      if e.get('type') == 'agent_stop'
                      and 'staff' in e.get('agent_name', '').lower()
                      and 'review' in e.get('agent_name', '').lower()])
                  if review_cycles_actual == 0:
                      review_cycles_actual = None  # null if no staff review spans found

      Add to env var block: SIDECAR_PATH_ENV="$SIDECAR_PATH"
      Add step_actuals and attribution_method to the printed JSON record:
          'step_actuals': step_actuals if step_actuals else None,
          'review_cycles_actual': review_cycles_actual,
          'attribution_method': attribution_method,
  - After successful history append, delete sidecar:
      if [ -n "$SIDECAR_PATH" ] && [ -f "$SIDECAR_PATH" ]; then
          rm -f "$SIDECAR_PATH"
      fi
  - Placement: sidecar deletion goes between "Recompute calibration factors" and "Clean up estimate"
```

### Change 5: .claude/settings.json — hook registration

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/settings.json
Lines: all (full file rewrite — small file, 35 lines currently)
Parallelism: depends-on: [Change 1 (agent-hook.sh)]
Description: Add PreToolUse Agent matcher hook and PostToolUse Agent second hook entry
  for tokencast-agent-hook.sh. Preserve all existing hooks.
Details:
  - PreToolUse array: add second entry with matcher "Agent" for agent-hook.sh
    (first entry remains the unmatched midcheck.sh)
  - PostToolUse Agent hooks array: add second entry for agent-hook.sh
    (first entry remains tokencast-track.sh)
  - Result:
    {
      "hooks": {
        "Stop": [
          { "hooks": [{ "type": "command", "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-learn.sh'" }] }
        ],
        "PostToolUse": [
          {
            "matcher": "Agent",
            "hooks": [
              { "type": "command", "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-track.sh'" },
              { "type": "command", "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-agent-hook.sh'" }
            ]
          }
        ],
        "PreToolUse": [
          { "hooks": [{ "type": "command", "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-midcheck.sh'" }] },
          {
            "matcher": "Agent",
            "hooks": [{ "type": "command", "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-agent-hook.sh'" }]
          }
        ]
      }
    }
  - Note: PreToolUse matcher support is unknown (OQ-1 in architecture). The agent-hook.sh
    self-filters on tool_name (exits early for non-Agent tools), so even if the matcher
    field is ignored and the hook fires on all PreToolUse events, correctness is preserved.
    Only Agent tool events produce sidecar writes; all others exit 0 immediately.
```

### Change 6: Version bump in SKILL.md

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/SKILL.md
Lines: line 3 (frontmatter version), line 386 (output template header)
Parallelism: depends-on: [Changes 1–5 complete]
Description: Bump version string from 1.6.0 to 1.7.0 in two places.
Details:
  - Line 3: version: 1.6.0 → version: 1.7.0
  - Line 386: ## costscope estimate (v1.6.0) → ## costscope estimate (v1.7.0)
  - No other SKILL.md changes for v1.7 (status invocation mode is v2.0)
```

---

## v1.7 Test Files

### Change 7: tests/test_agent_hook.py

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_agent_hook.py
Lines: new file (~250 lines)
Parallelism: independent (can be written in parallel with implementation)
Description: Unit and integration tests for tokencast-agent-hook.sh and
  the sum_session_by_agent() function.
Details:
  Test classes:

  TestSidecarEventSchema:
  - test_agent_start_event_fields: verify all required fields present in agent_start event
  - test_agent_stop_event_fields: verify all required fields present in agent_stop event
  - test_schema_version_is_1: schema_version == 1
  - test_agent_name_lowercased: agent_name is lowercased and stripped
  - test_parent_agent_null_for_top_level: parent_agent is null when not provided
  - test_metadata_is_empty_dict: metadata == {}

  TestAgentToStepMapping:
  - test_known_agent_names_map: researcher → "Research Agent", staff-reviewer → "Staff Review", etc.
  - test_engineer_ordinal_first: first "engineer" span → "Engineer Initial Plan"
  - test_engineer_ordinal_second: second "engineer" span → "Engineer Final Plan"
  - test_unrecognized_agent_raw_name: unknown agents stored under raw name

  TestSumSessionByAgent:
  - test_no_sidecar_returns_session_totals_only: None sidecar → no step_actuals key in result
  - test_missing_sidecar_returns_session_totals_only: nonexistent path → no step_actuals key
  - test_single_agent_span_full_session: one agent covers all lines → step_actuals sums to actual_cost
  - test_two_non_overlapping_agents: costs sum to session total within rounding
  - test_nested_agent_subtracted_from_parent: parent cost excludes child span lines
  - test_unattributed_lines_in_orchestrator: lines outside spans → _orchestrator key
  - test_empty_sidecar_returns_session_totals: empty sidecar → step_actuals empty or absent
  - test_malformed_sidecar_lines_skipped: json decode errors in sidecar → graceful
  - test_unknown_schema_version_skipped: schema_version != 1 events are ignored

  TestLearnShAgentHookIntegration (shell integration, skip if learn.sh absent):
  - test_step_actuals_written_to_history: run learn.sh with mock session JSONL + mock sidecar;
    verify history record has step_actuals dict
  - test_attribution_method_sidecar: history record has attribution_method == "sidecar"
  - test_attribution_method_proportional_fallback: no sidecar → attribution_method == "proportional"
  - test_review_cycles_actual_populated: sidecar with staff-reviewer agent_stop events →
    review_cycles_actual == count of those events
  - test_sidecar_deleted_after_processing: sidecar file removed after successful learn.sh run
  - test_orphan_sidecar_swept_on_next_run: sidecar older than 7 days → deleted by find -mtime +7

  TestAgentHookShellScript (requires bash; skip if script absent):
  - test_non_agent_tool_exits_early: stdin with tool_name="Read" → no sidecar written
  - test_pre_tool_use_writes_agent_start: stdin with hookEventName="PreToolUse", tool_name="Agent"
    → sidecar file created with agent_start event
  - test_post_tool_use_writes_agent_stop: stdin with hookEventName="PostToolUse", tool_name="Agent"
    → sidecar file contains agent_stop event
  - test_hook_is_fail_silent: bad stdin JSON → exits 0, no error
  - test_sidecar_file_path_uses_session_id: session_id in payload → sidecar named {session_id}-timeline.jsonl
```

### Change 8: tests/test_update_factors_excluded.py

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_update_factors_excluded.py
Lines: new file (~80 lines)
Parallelism: independent
Description: Tests for excluded field handling in update-factors.py.
Details:
  Test class: TestExcludedRecordHandling

  Helper: make_history_file(tmp_dir, records) — writes records to a temp history.jsonl

  Tests:
  - test_excluded_record_not_counted: record with excluded=true → not in factors sample_count
  - test_non_excluded_record_counted: record with excluded=false → counted normally
  - test_missing_excluded_field_defaults_to_included: no excluded key → treated as included
  - test_all_excluded_results_in_collecting: only excluded records → factors status "collecting"
  - test_excluded_does_not_affect_outlier_count: excluded record with high ratio → not in outlier_count
  - test_mix_excluded_and_clean_records: 3 clean + 1 excluded → sample_count == 3, factor computed
  - test_excluded_true_string_not_excluded: excluded="true" (string) → treated as included
    (only boolean True is excluded — .get('excluded', False) truthiness: "true" is truthy,
    so this test should actually confirm string "true" IS excluded — clarify behavior:
    use bool(record.get('excluded', False)) → truthy string excluded. Document in test.)
```

---

## v2.0 Changes

### Change 9: scripts/tokencast-status.py

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-status.py
Lines: new file (~500–600 lines)
Parallelism: independent (after v1.7 is merged; within v2.0 this is the first change)
Description: Pure-computation analysis script. Reads history.jsonl, factors.json, heuristics.md.
  Outputs structured JSON to stdout. No side effects, no file writes.
Details:

  Module-level constants:
  - OUTLIER_HIGH = 3.0, OUTLIER_LOW = 0.2 (match update-factors.py)
  - DEFAULT_WINDOW_SESSIONS = 10 (adaptive: show all if <= 10, rolling window if > 10)
  - DEFAULT_WINDOW_DAYS = 30
  - STALE_PRICING_DAYS = 90
  - Recommendation thresholds (mirrors architecture doc):
      REVIEW_CYCLES_HIGH_THRESHOLD = 0.5 (mean actual > default + this)
      REVIEW_CYCLES_HIGH_MIN_SESSIONS = 3
      BANDS_TOO_WIDE_PCT = 0.80 (>80% actuals within Optimistic band)
      BANDS_TOO_NARROW_PCT = 0.30 (>30% actuals exceed Pessimistic band)
      BANDS_MIN_SESSIONS = 5
      HIGH_OUTLIER_RATE_PCT = 0.50
      HIGH_OUTLIER_RATE_MIN_RECORDS = 6
      STEP_DOMINANCE_PCT = 0.60
      STEP_DOMINANCE_MIN_SESSIONS = 3

  Functions:

  parse_args() → argparse.Namespace:
    --history, --factors, --heuristics, --window, --verbose, --json, --no-apply
    Use argparse with /usr/bin/python3 compatibility (Python 3.9, stdlib only)

  load_history(path: str) → list[dict]:
    Parse JSONL, skip malformed lines, return all records (including excluded and outlier).
    Do not filter here — callers filter.

  load_factors(path: str) → dict:
    Load JSON; return {} if file absent.

  parse_heuristics_pricing_date(path: str) → str | None:
    Read heuristics.md, find "last_updated" or "Last Updated" field, return date string.
    Return None if not found. Used for stale pricing check.

  resolve_window(records: list[dict], window_spec: str | None) → list[dict]:
    Parse window_spec: None (adaptive), "30d" (days), "10" (count), "all" (all records)
    Adaptive: if total <= DEFAULT_WINDOW_SESSIONS → all; else rolling DEFAULT_WINDOW_DAYS days
    or last DEFAULT_WINDOW_SESSIONS, whichever is larger
    Return windowed list (does NOT filter excluded records here — callers handle)

  compute_health(records: list[dict], factors: dict) → dict:
    Separate outliers from clean records (same thresholds as update-factors.py)
    Determine active_factor_level from factors dict (check per-step → signature → size-class → global)
    Return health dict matching JSON schema

  compute_accuracy(records: list[dict], verbose: bool) → dict:
    Filter to clean, non-excluded records
    Compute mean, median, trend (compare first half vs second half ratios; improving if delta > 0.05)
    pct_within_expected: ratio of records where actual <= expected_cost * 1.0 (band hit = expected)
    Actually band_hit: "optimistic" if actual <= optimistic_cost, "expected" if actual <= pessimistic_cost
    (use stored expected_cost/pessimistic_cost/optimistic_cost from record if present,
     or estimate from ratio and expected_cost)
    Return accuracy dict matching JSON schema

  compute_cost_attribution(records: list[dict]) → dict:
    Filter to records with step_actuals != null
    Aggregate per-step actual totals and estimated totals
    Sort by actual_total descending
    Return cost_attribution dict

  compute_outliers(all_records: list[dict], windowed_records: list[dict]) → dict:
    Filter all_records to outliers (ratio > OUTLIER_HIGH or < OUTLIER_LOW, not excluded)
    Compute outlier rate = outlier_count / total_records
    Pattern detection: check if multiple outliers share size, project_type; emit pattern strings
    Return outliers dict

  compute_recommendations(records: list[dict], factors: dict, heuristics_path: str,
                           review_cycles_default: int) → list[dict]:
    Run each recommendation rule function (see below), collect non-None results
    Sort by priority, then by data point count (tiebreaker)
    Return list of recommendation dicts

  Individual recommendation functions (each returns None or recommendation dict):
    rec_review_cycles_high(records, review_cycles_default) → dict | None:
      Require >= REVIEW_CYCLES_HIGH_MIN_SESSIONS records with review_cycles_actual non-null
      Mean actual > review_cycles_default + REVIEW_CYCLES_HIGH_THRESHOLD → fire
      proposed_value = int(ceil(mean_actual))
      impact_estimate: recompute pct_within_expected with N=proposed_value vs current
    rec_bands_too_wide(records) → dict | None:
      Require >= BANDS_MIN_SESSIONS clean records
      Count records where ratio <= (optimistic/expected ratio ≈ 0.6) → if > BANDS_TOO_WIDE_PCT → fire
      Action: guidance only (no file edit)
    rec_bands_too_narrow(records) → dict | None:
      Require >= BANDS_MIN_SESSIONS clean records
      Count records where ratio > 3.0 → if > BANDS_TOO_NARROW_PCT → fire
      Action: guidance only
    rec_high_outlier_rate(all_records) → dict | None:
      Require >= HIGH_OUTLIER_RATE_MIN_RECORDS total records
      outlier_count / total > HIGH_OUTLIER_RATE_PCT → fire
      Action: reset_calibration (destructive)
    rec_step_dominance(records) → dict | None:
      Require >= STEP_DOMINANCE_MIN_SESSIONS records with step_actuals
      Find step with > STEP_DOMINANCE_PCT of total spend → fire
      Action: guidance only
    rec_stale_pricing(heuristics_path) → dict | None:
      Parse pricing date from heuristics.md (or references/pricing.md)
      If > STALE_PRICING_DAYS old → fire
      Action: guidance only
    rec_session_outlier(windowed_records) → list[dict]:
      For each record in windowed_records with ratio > OUTLIER_HIGH or < OUTLIER_LOW:
        Emit individual recommendation with action: exclude_session
      Returns list (multiple can fire), not single dict

  parse_review_cycles_default(heuristics_path: str) → int:
    Read heuristics.md, find "review_cycles_default" line, parse value
    Return 2 (default) if not found

  analyze(args: argparse.Namespace) → dict:
    Load data, resolve window, compute all sections, build and return the full JSON output dict

  main():
    args = parse_args()
    if not Path(args.history).exists():
        # not_enough_data condition handled in analyze()
        pass
    result = analyze(args)
    print(json.dumps(result, indent=2))

  sparse/empty handling in analyze():
    all_records = load_history(history_path)  # may be empty
    clean_non_excluded = [r for r in all_records if not r.get('excluded') and
                          OUTLIER_LOW <= r.get('actual_cost',0)/max(r.get('expected_cost',0.001),0.001) <= OUTLIER_HIGH]
    If len(all_records) == 0 and not verbose:
        return {"schema_version": 1, ..., "health": {"status": "no_data", "message": "Not enough data yet..."}, ...}
    If len(clean_non_excluded) < 3 and not verbose:
        return not_enough_data response with count and how many more needed
    If all records are outliers and not verbose:
        return not_enough_data response noting all flagged as outliers
    Otherwise: compute all sections normally (verbose bypasses gate)
```

### Change 10: SKILL.md — status invocation mode

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/SKILL.md
Lines: "When This Skill Activates" section (lines ~20–30), new "Status Mode" section after
  existing "Overrides" section (after line ~451), version bump (lines 3 and 386)
Parallelism: depends-on: [Change 9]
Description: Add status invocation detection and the full status flow. Bump version to 2.0.0.
Details:
  - "When This Skill Activates" — add bullet:
      - The user types `/tokencast status` (any flags), which triggers status mode
        instead of estimation mode
  - "Do NOT activate" — add bullet:
      - The conversation just completed a status invocation (avoid re-triggering)
  - New section: ## Status Mode (invoked via /tokencast status)
    Content:
      When the invocation contains "status" as the first argument after /tokencast,
      enter status mode. Skip all estimation steps (Step 0 through Step 4).

      1. Parse flags from invocation text:
         - `--verbose` → pass --verbose to script
         - `--json` → pass --json; output raw JSON and stop (skip formatting)
         - `--no-apply` → pass --no-apply; show recommendations without prompts
         - `window=30d`, `window=10`, `window=all` → pass as --window 30d / --window 10 / --window all

      2. Determine file paths:
         - SCRIPT_DIR: derive from SKILL.md location (same directory as scripts/)
         - history_path: calibration/history.jsonl (relative to SKILL_DIR)
         - factors_path: calibration/factors.json
         - heuristics_path: references/heuristics.md

      3. Run analysis:
         ```
         python3 scripts/tokencast-status.py \
           --history calibration/history.jsonl \
           --factors calibration/factors.json \
           --heuristics references/heuristics.md \
           [--window <spec>] [--verbose] [--json] [--no-apply]
         ```
         Capture JSON output. If exit code != 0 or output is not parseable JSON:
         respond "Status analysis failed — check that history.jsonl and scripts/ are present."

      4. If --json flag: output raw JSON. Done.

      5. Format dashboard (5 sections in order):
         Section 1 — Health Summary:
           Display health.message in a blockquote or bold line.
         Section 2 — Accuracy Trend:
           Table: Session | Size | Ratio | Expected | Actual | Band
           Show trend classification (improving/stable/degrading) with emoji-free indicator
           Show mean_ratio, median_ratio, pct_within_expected (as %), pct_within_pessimistic (as %)
         Section 3 — Cost Attribution:
           If has_step_data=false: show "No per-agent step data yet (pre-v1.7 sessions)"
           If has_step_data=true: table: Step | Total Spend | % of Total | Accuracy
           Note: show sessions_without_step_data if > 0
         Section 4 — Outlier Report:
           If count=0: show "No outliers in this window."
           Else: table of outlier sessions with ratio, costs, probable_cause
           Show patterns if any
         Section 5 — Recommendations:
           For each recommendation in order:
             Display description and supporting_data in plain language
             If --no-apply: show proposed action in brackets, continue to next
             If NOT --no-apply:
               If destructive=false: prompt "Apply? [y/N]"
                 If y: execute action (see Apply Actions below)
               If destructive=true: prompt "Apply? [y/N]"
                 If y: show warning explaining what will be deleted/changed
                        second prompt "Are you sure? This will {description}. Proceed? [y/N]"
                        If y: execute destructive action

      6. Apply Actions (executed by Claude using file tools):
         edit_heuristic:
           - Read action.file (heuristics.md)
           - Find line containing action.parameter and action.current_value
           - Replace current_value with action.proposed_value
           - Write file
           - If parameter not found: respond "Could not locate parameter in heuristics.md --
             please edit manually: change {parameter} from {current_value} to {proposed_value}"
           - After edit: re-run status script with --json, compute before/after accuracy diff,
             show "Before: {pct_within_expected_before}% of sessions within Expected band /
             After: {pct_within_expected_after}% of sessions within Expected band"
         exclude_session:
           - Read calibration/history.jsonl
           - Find the record matching action.session_timestamp (match on "timestamp" field)
           - Add "excluded": true to that record
           - Rewrite history.jsonl (in-place; history.jsonl is append-only but in-place rewrite
             is acceptable here as it only adds a field to one line)
           - Re-run update-factors.py to recompute factors with excluded record removed
           - Show before/after accuracy diff
         reset_calibration:
           - Run: rm calibration/history.jsonl calibration/factors.json
           - Respond "Calibration reset. history.jsonl and factors.json deleted."
           - Do NOT delete sidecar files (they belong to the current session)

  - Version bump: 1.7.0 → 2.0.0 in frontmatter (line 3) and output template header (line 386)
```

---

## v2.0 Test Files

### Change 11: tests/test_status_analysis.py

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_status_analysis.py
Lines: new file (~350–400 lines)
Parallelism: independent (can be written in parallel with Change 9 since interface is defined)
Description: Unit tests for tokencast-status.py analysis logic and JSON output.
Details:
  Test classes:

  TestHealthComputation:
  - test_no_data_status: empty history → status "no_data"
  - test_collecting_status_1_record: 1 record → status "collecting"
  - test_active_status_3_clean_records: 3+ clean → status "active"
  - test_all_outliers_not_enough_data: all records outliers → special "all_outliers" state
  - test_active_factor_level_per_step: factors with step_factors active → level "per-step"
  - test_active_factor_level_global: only global → level "global"
  - test_active_factor_level_none: no factors.json → level "none"

  TestAccuracyComputation:
  - test_mean_and_median_ratio: 3 records with known ratios → correct mean/median
  - test_trend_improving: ratios decreasing toward 1.0 → trend "improving"
  - test_trend_stable: ratios within ±10% of 1.0 → trend "stable"
  - test_trend_degrading: ratios increasing away from 1.0 → trend "degrading"
  - test_pct_within_expected: 2 of 3 sessions within expected band → 0.667
  - test_pct_within_pessimistic: all within pessimistic → 1.0
  - test_insufficient_data_trend: 1 session → trend "insufficient_data"

  TestCostAttribution:
  - test_no_step_data: no records with step_actuals → has_step_data False
  - test_step_totals_aggregated: 2 sessions with step_actuals → sums correct
  - test_sorted_by_cost_descending: highest spend step appears first
  - test_accuracy_ratio_per_step: actual/estimated per step
  - test_mixed_v17_and_pre_v17: some records lack step_actuals → sessions_without_step_data count
  - test_note_when_missing_step_data: sessions_without_step_data > 0 → note field non-null

  TestOutlierReport:
  - test_no_outliers: all clean → count 0
  - test_high_ratio_outlier: ratio > 3.0 → included in outliers
  - test_low_ratio_outlier: ratio < 0.2 → included
  - test_outlier_rate_calculation: 2 outliers / 10 total → rate 0.2
  - test_excluded_not_in_outliers: excluded=true record not double-counted as outlier

  TestRecommendations:
  - test_review_cycles_high_fires: 3+ records with actual > default+0.5 → rec emitted
  - test_review_cycles_high_insufficient_data: 2 records → not emitted
  - test_review_cycles_high_not_fires_when_low: actual == default → not emitted
  - test_bands_too_wide_fires: >80% within optimistic → rec emitted
  - test_bands_too_wide_insufficient_data: < 5 sessions → not emitted
  - test_high_outlier_rate_fires: >50% outliers, >= 6 records → rec emitted
  - test_high_outlier_rate_destructive: rec has destructive=true
  - test_step_dominance_fires: one step > 60% of spend → rec emitted
  - test_stale_pricing_fires: pricing > 90 days old → rec emitted
  - test_recommendation_ordering: impact (accuracy-affecting) before guidance before informational
  - test_no_false_positives_sparse: < 3 data points → no recommendations

  TestWindowResolution:
  - test_adaptive_small_history: 8 records → all returned
  - test_adaptive_large_history: 15 records → rolling 30-day or last 10, whichever larger
  - test_window_days_spec: window="30d" → only records within last 30 days
  - test_window_count_spec: window="10" → last 10 records
  - test_window_all_spec: window="all" → all records
  - test_empty_window: window spec matches no records → empty list (no crash)

  TestSparseBehavior:
  - test_no_history_file: missing file → not_enough_data response
  - test_one_record_not_verbose: 1 record → not_enough_data message
  - test_one_record_verbose: 1 record + --verbose → shows partial data with "preliminary" label
  - test_all_outliers_not_verbose: all outliers → not_enough_data with outlier note
  - test_all_outliers_verbose: all outliers + --verbose → shows data

  TestJsonOutput:
  - test_json_schema_version: output has schema_version=1
  - test_json_parseable: full output is valid JSON
  - test_json_required_top_level_keys: health, accuracy, cost_attribution, outliers, recommendations present
  - test_json_no_interactive_prompts: --json flag → no "Apply?" text in output

  TestStatusScriptIntegration (shell integration):
  - test_script_invocation_with_history: run script with mock history.jsonl → exit 0, valid JSON
  - test_not_enough_data_message: 1-record history → not_enough_data in health.message
  - test_verbose_flag_bypasses_gate: 1-record + --verbose → sections present
  - test_json_flag_raw_output: --json → parseable JSON, no markdown
  - test_no_apply_flag_in_output: --no-apply included in flags → recommendations present, no prompts
```

---

## Dependency Order

### v1.7 Execution Order

**Parallel batch 1** (no dependencies, can run concurrently):
- Change 1: tokencast-agent-hook.sh (new file)
- Change 2: sum-session-tokens.py (new function)
- Change 3: update-factors.py (excluded field)
- Change 7: tests/test_agent_hook.py (write tests while implementation is in progress)
- Change 8: tests/test_update_factors_excluded.py

**Sequential after batch 1**:
- Change 4: tokencast-learn.sh (depends on Changes 1 and 2)
- Change 5: .claude/settings.json (depends on Change 1)

**Sequential after Changes 4 and 5**:
- Change 6: SKILL.md version bump (depends on Changes 1–5 complete)

### v2.0 Execution Order

**After v1.7 is merged:**

**Parallel batch 2**:
- Change 9: tokencast-status.py (new file)
- Change 11: tests/test_status_analysis.py (can be written concurrently since interface is specified)

**Sequential after batch 2**:
- Change 10: SKILL.md status invocation mode + version bump to 2.0.0 (depends on Change 9)

---

## Test Strategy

### New test files
- `tests/test_agent_hook.py` — covers agent-hook.sh (shell integration) and sum_session_by_agent()
- `tests/test_update_factors_excluded.py` — covers excluded field in update-factors.py
- `tests/test_status_analysis.py` — covers tokencast-status.py analysis logic

### Existing tests that may need updating
- `tests/test_per_step_factors.py`: The `TestLearnShIntegrationStepCosts` class tests learn.sh's
  step_ratios computation. The proportional attribution logic is unchanged (it becomes the fallback),
  so these tests should still pass. Verify that the new SIDECAR_PATH_ENV env var doesn't interfere
  with integration tests that don't provide a sidecar (sidecar_path empty → proportional fallback).
- `tests/test_parallel_agent_accounting.py`: No changes expected; parallel discount logic is unchanged.
- All other existing tests: run full suite after each change group to catch regressions.

### Edge cases to cover
- Hook fires on non-Agent tools: exits 0, no sidecar written
- Sidecar exists but is empty: sum_session_by_agent returns no step_actuals
- Sidecar has malformed JSON lines: graceful skip, no crash
- Nested agents: parent cost correctly excludes child span
- learn.sh with no sidecar: proportional attribution, attribution_method="proportional"
- update-factors.py with mix of excluded and clean records: only clean counted
- Status command with no history.jsonl: not_enough_data response
- Status command --verbose with 1 record: partial data with label
- Recommendation minimum data guards: no false positives below threshold

### Test runner
All tests use `/usr/bin/python3 -m pytest tests/` (system Python 3.9 with pytest).
Do not use `pytest` or `python3 -m pytest` directly (Homebrew Python 3.14 lacks pytest).

---

## Rollback Notes

### v1.7 rollback
- `tokencast-agent-hook.sh`: delete the file. Hook is fail-silent — removing it stops event capture but does not break any existing functionality.
- `settings.json`: remove the two new hook entries (PreToolUse Agent matcher, PostToolUse second Agent hook). The file is small and hand-editable.
- `sum-session-tokens.py`: the new `sum_session_by_agent()` function and AGENT_TO_STEP constant are additive. Revert using `git revert` or `git checkout` the file to prior commit.
- `tokencast-learn.sh`: revert to prior commit. The VERSION string, sidecar discovery block, and RECORD Python block changes are all in one file.
- `update-factors.py`: revert the single `if record.get('excluded', False): continue` addition.
- `SKILL.md`: revert version string only (two lines).
- Data: sidecar files in `calibration/` are transient and gitignored. history.jsonl records gain new fields (`step_actuals`, `attribution_method`) but are backward compatible — old code ignores unknown fields via `.get()`.

### v2.0 rollback
- `tokencast-status.py`: delete the file.
- `SKILL.md`: revert status invocation mode section and version bump.
- `calibration/history.jsonl`: records with `excluded: true` added by the status command can be manually edited to remove the field, or left in place (update-factors.py will skip them — revert that behavior only if rolling back Change 3).
- No destructive data operations: reset_calibration deletes history.jsonl and factors.json, but that action is user-initiated and confirmed. No automated rollback needed for that action.
