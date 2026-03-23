# Implementation Plan: v1.7 Per-Agent Step Actuals + v2.0 /tokencostscope status (Final)

## Staff Review Findings — Disposition

All 15 findings addressed below. Each change section notes the specific finding(s) applied.

| Finding | Severity | Resolution |
|---------|----------|------------|
| F1: Remove parent_agent; infer nesting chronologically | HIGH | Applied in Changes 1, 2, 7 |
| F2: FIFO span matching + span_id counter | HIGH | Applied in Changes 1, 2, 7 |
| F3: Explicit session_expected variable to avoid shadowing | HIGH | Applied in Change 4 |
| F4: Single-pass JSONL with shared compute_line_cost() helper | HIGH | Applied in Change 2 |
| F5: Capture stdin to variable first (STDIN_JSON=$(cat)) | MEDIUM | Applied in Change 1 |
| F6: printf '%s' for hashing; pin hash algorithm cross-platform | MEDIUM | Applied in Changes 1, 4 |
| F7: Explicit sidecar_path check in main() | MEDIUM | Applied in Change 2 |
| F8: 3+ engineer spans: last → Final Plan, extras raw | MEDIUM | Applied in Change 2 |
| F9: Fix test name; assert string "true" IS excluded | MEDIUM | Applied in Change 8 |
| F10: Add optimistic_cost/pessimistic_cost to history record | MEDIUM | Applied in Changes 4, 9 |
| F11: rec_session_outlier returns flat list; compute_recommendations calls per-record | MEDIUM | Applied in Change 9 |
| F12: Separate matcher entries for PostToolUse hooks | LOW | Applied in Change 5 |
| F13: Add SIDECAR_PATH_ENV to integration test env setup | LOW | Applied in Change 7 |
| F14: Use stored ratio field from history records | LOW | Applied in Change 9 |
| F15: Add test_zero_width_span_no_cost test case | LOW | Applied in Change 7 |

## Enterprise Design Constraints — Disposition

Three constraints added after staff review. Additive — do not alter core changes above.

| Constraint | Resolution |
|-----------|------------|
| E1: AGENT_TO_STEP must be configurable, not hardcoded | New Change 12 (calibration/agent-map.json); Change 2 loads it at runtime with hardcoded dict as fallback |
| E2: Calibration storage abstraction | New Change 13 (scripts/calibration_store.py); learn.sh and status.py route all reads/writes through it |
| E3: Sidecar and JSON output schemas are API contracts | No new file; explicit contract note added to Change 1 and Change 9. schema_version is a breaking-change gate. |

---

## Overview

v1.7 adds hook-based per-agent cost attribution via a sidecar timeline file, enabling true per-step actual/expected cost ratios. v2.0 builds a `/tokencostscope status` dashboard on top of v1.7 data. The two versions ship sequentially: v1.7 is implemented and merged first; v2.0 follows.

Within v1.7, Changes 1, 2, 3, 12, and 13 are independent and can run in parallel. learn.sh (Change 4) depends on Changes 1, 2, and 13. settings.json (Change 5) depends on Change 1. Tests can be written in parallel once interfaces are defined.

---

## Enterprise Design Context

These three constraints are not user-visible features. They are structural decisions that make the codebase ready for enterprise deployment without requiring a future rewrite.

**E1 — Configurable agent mapping:** Enterprise teams use different agent names than the defaults in CLAUDE.md (e.g., "impl-backend", "impl-frontend" instead of "implementer"). The AGENT_TO_STEP dict must be overridable per-project without editing source code.

**E2 — Storage abstraction:** Today, calibration data lives in `calibration/` on local disk. Enterprise deployments will route reads and writes through a remote API (authentication, multi-team aggregation, server-side factor computation). Concentrating all storage interactions in one module now means the future adapter only replaces one file, not a dozen inline shell/Python snippets scattered across learn.sh, status.py, and update-factors.py.

**E3 — Schema contracts:** The sidecar event schema and the status JSON output schema are the stable interfaces between the data-collection layer, the analysis layer, and downstream consumers. Once v1 ships, consumers may build on these schemas. Breaking a v1 schema without bumping to v2 would silently corrupt downstream tools. This constraint makes the versioning promise explicit in the code.

---

## v1.7 Changes

### Change 1: scripts/tokencostscope-agent-hook.sh

**Findings applied: F1, F2, F5, F6**
**Enterprise constraint: E3 (schema contract note)**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencostscope-agent-hook.sh
Lines: new file
Parallelism: independent
Description: New bash hook script handling both PreToolUse and PostToolUse events on the
  Agent tool. Writes agent_start / agent_stop events to a per-session sidecar timeline JSONL.
  Fail-silent via || exit 0. No parent_agent field in schema (F1). Includes span_id (F2).
  Reads stdin into variable before passing to Python (F5). Uses printf '%s' with
  cross-platform hash (F6).
Details:
  - Shebang: #!/usr/bin/env bash
  - set -euo pipefail at top; entire body wrapped in { ... } || exit 0 so hook never blocks

  STDIN capture (F5 — same pattern as midcheck.sh):
    STDIN_JSON=$(cat)
    # From this point, extract all fields from $STDIN_JSON via env var

  Field extraction via python3 -c inline (pass STDIN_JSON via env var):
    HOOK_FIELDS=$(HOOK_STDIN="$STDIN_JSON" python3 -c "
    import json, os, shlex
    try:
        d = json.loads(os.environ['HOOK_STDIN'])
    except Exception:
        print('HOOK_EVENT_NAME=')
        print('TOOL_NAME=')
        print('AGENT_NAME=')
        print('SESSION_ID=')
        raise SystemExit(0)
    print(f'HOOK_EVENT_NAME={shlex.quote(str(d.get(\"hookEventName\", \"\")))}')
    print(f'TOOL_NAME={shlex.quote(str(d.get(\"toolName\", d.get(\"tool_name\", \"\"))))}')
    ti = d.get(\"tool_input\") or {}
    print(f'AGENT_NAME={shlex.quote(str(ti.get(\"name\", \"\")).lower().strip())}')
    print(f'SESSION_ID={shlex.quote(str(d.get(\"session_id\", \"\")))}')
    " 2>/dev/null) || exit 0
    eval "$HOOK_FIELDS"

  Self-filter (F1 — no parent_agent needed; hook records top-level events only):
    [ "$TOOL_NAME" = "Agent" ] || exit 0
    [ -n "$AGENT_NAME" ] || exit 0

  Path setup:
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    SKILL_DIR="$(dirname "$SCRIPT_DIR")"
    CALIBRATION_DIR="$SKILL_DIR/calibration"
    mkdir -p "$CALIBRATION_DIR"

  Session ID and sidecar path:
    SESSION_ID from payload; if empty, compute deterministic fallback (F6):
      if [ -z "$SESSION_ID" ]; then
          # Use the estimate file path as stable per-session input
          HASH_INPUT="$CALIBRATION_DIR/active-estimate.json"
          if command -v md5 >/dev/null 2>&1; then
              SESSION_ID=$(printf '%s' "$HASH_INPUT" | md5 | cut -c1-12)
          elif command -v md5sum >/dev/null 2>&1; then
              SESSION_ID=$(printf '%s' "$HASH_INPUT" | md5sum | cut -c1-12)
          else
              SESSION_ID="unknown-$$"
          fi
      fi
    SIDECAR_FILE="$CALIBRATION_DIR/${SESSION_ID}-timeline.jsonl"

  JSONL line count for span attribution:
    JSONL_PATH=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f \
        -newer "$CALIBRATION_DIR" -print0 2>/dev/null | \
        xargs -0 ls -t 2>/dev/null | head -1 || echo "")
    if [ -n "$JSONL_PATH" ] && [ -f "$JSONL_PATH" ]; then
        JSONL_LINE_COUNT=$(wc -l < "$JSONL_PATH" 2>/dev/null || echo 0)
    else
        JSONL_LINE_COUNT=0
    fi

  span_id counter (F2 — incrementing per-file counter for unambiguous matching):
    # Read current span_id counter from a small counter file; increment atomically
    COUNTER_FILE="$CALIBRATION_DIR/${SESSION_ID}-span-counter"
    SPAN_ID=1
    if [ -f "$COUNTER_FILE" ]; then
        PREV=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
        SPAN_ID=$(( PREV + 1 ))
    fi
    echo "$SPAN_ID" > "$COUNTER_FILE"

  Event type:
    EVENT_TYPE="agent_start"
    [ "$HOOK_EVENT_NAME" = "PostToolUse" ] && EVENT_TYPE="agent_stop"

  Timestamp:
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

  Build EVENT_JSON via python3 -c inline using env vars (shlex-safe):
    EVENT_JSON=$(TYPE_ENV="$EVENT_TYPE" TS_ENV="$TIMESTAMP" AGENT_ENV="$AGENT_NAME" \
        SID_ENV="$SESSION_ID" LC_ENV="$JSONL_LINE_COUNT" SPID_ENV="$SPAN_ID" python3 -c "
    import json, os
    print(json.dumps({
        'schema_version': 1,
        'type': os.environ['TYPE_ENV'],
        'timestamp': os.environ['TS_ENV'],
        'agent_name': os.environ['AGENT_ENV'],
        'session_id': os.environ['SID_ENV'],
        'jsonl_line_count': int(os.environ['LC_ENV']),
        'span_id': int(os.environ['SPID_ENV']),
        'metadata': {}
    }))
    " 2>/dev/null) || exit 0

  NOTE (F1): No parent_agent field. Nesting is inferred in sum_session_by_agent() from the
  chronological order of open spans: when an agent_start fires while another agent's span is
  still open (no matching agent_stop yet), the open agent is treated as parent. This is purely
  a read-time computation — no parent tracking at write time.

  Atomic append:
    echo "$EVENT_JSON" >> "$SIDECAR_FILE"
    # POSIX guarantees atomic appends < PIPE_BUF (~4096 bytes). Each event ~300 bytes.

  Comment block at top of file (includes E3 contract notice):
    # Expected hook payload fields (update this comment if Claude Code payload format changes):
    #   hookEventName: "PreToolUse" | "PostToolUse"
    #   toolName / tool_name: "Agent" (we self-filter on this)
    #   tool_input.name: agent name string
    #   session_id: session identifier (may be absent — see fallback above)
    #
    # SCHEMA CONTRACT (E3): The sidecar event schema (schema_version=1) is a versioned API
    # contract. Fields may be ADDED to v1 events without breaking readers (additive-only).
    # Removing or renaming existing fields, or changing their types, requires bumping
    # schema_version to 2 and updating all readers (sum-session-tokens.py, tests).
    # Do not break v1 without a migration plan.
```

### Change 2: scripts/sum-session-tokens.py — new sum_session_by_agent function

**Findings applied: F1, F2, F4, F7, F8**
**Enterprise constraint: E1 (AGENT_TO_STEP loaded from config at runtime)**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/sum-session-tokens.py
Lines: new helper function compute_line_cost() before sum_session(); new function
  sum_session_by_agent() after sum_session(); new _load_agent_map() loader; main() updated.
  Approximately 110–130 new lines.
Parallelism: independent
Description: Add shared compute_line_cost() helper (F4), sum_session_by_agent() with
  chronological nesting inference (F1), FIFO span matching via span_id (F2), explicit
  sidecar check in main() (F7), 3+ engineer disambiguation rule (F8), runtime-loadable
  AGENT_TO_STEP from calibration/agent-map.json (E1).
Details:

  DEFAULT_AGENT_TO_STEP mapping dict (module-level constant — hardcoded fallback only):
    DEFAULT_AGENT_TO_STEP = {
        "researcher": "Research Agent",
        "research": "Research Agent",
        "architect": "Architect Agent",
        # "engineer" is handled by ordinal disambiguation — not in this dict
        "engineer-initial": "Engineer Initial Plan",   # explicit unambiguous name
        "engineer-final": "Engineer Final Plan",       # explicit unambiguous name
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
    # "engineer" is absent. Ordinal disambiguation happens in _build_spans().
    # Users should name agents "engineer-initial" / "engineer-final" to be unambiguous.
    # Enterprise teams: add custom names to calibration/agent-map.json (see E1/Change 12).

  New loader: _load_agent_map(calibration_dir: str) -> dict:  (E1)
    """Load agent-to-step mapping. Merges DEFAULT_AGENT_TO_STEP with calibration/agent-map.json.

    The config file wins over defaults for any key that appears in both.
    Keys in DEFAULT_AGENT_TO_STEP that do not appear in the config are preserved.
    This means enterprise teams only need to specify their custom names — they do not
    need to replicate the full default table.

    Returns the merged dict. If agent-map.json is absent or malformed, returns
    DEFAULT_AGENT_TO_STEP unchanged (fail-open: missing config is not an error).
    """
    map_path = os.path.join(calibration_dir, "agent-map.json")
    merged = dict(DEFAULT_AGENT_TO_STEP)
    if not os.path.exists(map_path):
        return merged
    try:
        with open(map_path) as f:
            overrides = json.load(f)
        if isinstance(overrides, dict):
            # Lowercase all override keys for consistent lookup
            merged.update({k.lower().strip(): v for k, v in overrides.items()})
    except (json.JSONDecodeError, OSError):
        pass  # fail-open: return defaults
    return merged

  AGENT_TO_STEP is NOT a module-level constant. It is loaded per-call by passing
  calibration_dir to _build_spans() and sum_session_by_agent(). This allows tests
  to inject a custom calibration_dir with a different agent-map.json without
  monkey-patching module state.

  New helper: compute_line_cost(obj: dict) -> float (F4 — single-pass shared helper):
    """Compute dollar cost for one parsed JSONL assistant message object.
    Returns 0.0 if the object is not a billable assistant message.
    Used by both sum_session() and sum_session_by_agent() to ensure cost
    calculations are identical and the per-agent totals sum to the session total.
    """
    if obj.get("type") != "assistant":
        return 0.0
    msg = obj.get("message", {})
    usage = msg.get("usage")
    if not usage:
        return 0.0
    model = msg.get("model", "")
    if not model or model == "<synthetic>":
        return 0.0
    model_key = model
    for known in PRICES:
        if known in model:
            model_key = known
            break
    prices = PRICES.get(model_key, PRICES[DEFAULT_MODEL])
    inp = usage.get("input_tokens", 0)
    cr = usage.get("cache_read_input_tokens", 0)
    cw = usage.get("cache_creation_input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cost = (
        inp * prices["input"]
        + cr * prices["cache_read"]
        + cw * prices["cache_write"]
        + out * prices["output"]
    ) / 1_000_000
    return cost

  Refactor sum_session() to use compute_line_cost() (F4):
    Replace per-model accumulation loop with calls to compute_line_cost().
    sum_session() retains its return signature. tokens_by_model is removed from
    return dict (unused by callers). Internal only:
      total_cost = 0.0
      turn_count = 0
      for line in f:
          try: obj = json.loads(line)
          except: continue
          cost = compute_line_cost(obj)
          if cost > 0:
              total_cost += cost
              turn_count += 1

  New function: _build_spans(sidecar_path: str, agent_to_step: dict) -> dict:
    """Parse sidecar JSONL and return effective_ranges: {step_name: [(start, end), ...]}

    Nesting inference (F1): open span = open agent_start with no matching stop yet.
    When a new agent_start fires, the innermost open span is its parent.
    Child ranges are subtracted from parent effective ranges.

    FIFO matching (F2): for each agent_stop, pop oldest unmatched start for same agent_name
    by span_id order.

    Engineer disambiguation (F8): first "engineer" → Initial Plan, second → Final Plan,
    third+ → raw name. "engineer-initial" / "engineer-final" map directly via agent_to_step.
    """
    events = []
    with open(sidecar_path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: ev = json.loads(line)
            except json.JSONDecodeError: continue
            if ev.get("schema_version") != 1: continue
            if ev.get("type") not in ("agent_start", "agent_stop"): continue
            events.append(ev)

    events.sort(key=lambda e: (e.get("span_id", 0), e.get("timestamp", "")))

    from collections import defaultdict
    open_starts = defaultdict(list)  # agent_name → [start_event, ...]
    open_spans = []                  # (agent_name, start_line, span_id) — ordered by start
    completed_spans = []             # {step_name, start_line, end_line, parent_step}
    engineer_count = 0

    for ev in events:
        ev_type = ev.get("type")
        agent_name = ev.get("agent_name", "")
        line_count = ev.get("jsonl_line_count", 0)
        span_id = ev.get("span_id", 0)

        if ev_type == "agent_start":
            parent_name = open_spans[-1][0] if open_spans else None
            open_starts[agent_name].append({
                "start_line": line_count,
                "span_id": span_id,
                "parent_name": parent_name,
            })
            open_spans.append((agent_name, line_count, span_id))

        elif ev_type == "agent_stop":
            if not open_starts[agent_name]: continue
            start_ev = open_starts[agent_name].pop(0)
            open_spans = [(n, sl, sid) for (n, sl, sid) in open_spans
                          if not (n == agent_name and sid == start_ev["span_id"])]

            # Resolve step name
            step_name = agent_to_step.get(agent_name)
            if agent_name == "engineer" or (step_name is None and agent_name == "engineer"):
                engineer_count += 1
                if engineer_count == 1: step_name = "Engineer Initial Plan"
                elif engineer_count == 2: step_name = "Engineer Final Plan"
                else: step_name = agent_name
            elif step_name is None:
                step_name = agent_name

            completed_spans.append({
                "step_name": step_name,
                "start_line": start_ev["start_line"],
                "end_line": line_count,
                "parent_name": start_ev["parent_name"],
            })

    # Unmatched starts: give end_line = last recorded line count
    total_lines = max((ev.get("jsonl_line_count", 0) for ev in events), default=0)
    for agent_name, starts_list in open_starts.items():
        for start_ev in starts_list:
            step_name = agent_to_step.get(agent_name, agent_name)
            completed_spans.append({
                "step_name": step_name,
                "start_line": start_ev["start_line"],
                "end_line": total_lines,
                "parent_name": start_ev["parent_name"],
            })

    # Build effective ranges: subtract child spans from parent spans
    spans_by_step = {}
    all_child_ranges = {}  # parent_name → [(start, end)]
    for sp in completed_spans:
        step = sp["step_name"]
        spans_by_step.setdefault(step, []).append((sp["start_line"], sp["end_line"]))
        if sp["parent_name"]:
            all_child_ranges.setdefault(sp["parent_name"], []).append(
                (sp["start_line"], sp["end_line"]))

    effective_ranges = {}
    for step_name, raw_ranges in spans_by_step.items():
        child_ranges = all_child_ranges.get(step_name, [])
        result_ranges = []
        for (rs, re) in raw_ranges:
            result_ranges.extend(_subtract_ranges(rs, re, child_ranges))
        effective_ranges[step_name] = sorted(result_ranges)

    return effective_ranges

  Helper: _subtract_ranges(start, end, children) -> list of (start, end):
    (same as in initial final plan — returns non-overlapping gaps after subtracting children)

  New function: sum_session_by_agent(jsonl_path: str, sidecar_path: str,
                                      baseline_cost: float = 0.0,
                                      calibration_dir: str = None) -> dict:
    """Single-pass JSONL attribution (F4). calibration_dir used to load agent-map.json (E1)."""
    agent_to_step = _load_agent_map(calibration_dir or os.path.dirname(sidecar_path))
    effective_ranges = _build_spans(sidecar_path, agent_to_step)

    # Build sorted flat list of (start_line, end_line, step_name)
    all_ranges = sorted(
        (s, e, step) for step, ranges in effective_ranges.items() for (s, e) in ranges
    )

    def find_step(line_num):
        for (s, e, step) in all_ranges:
            if s <= line_num < e: return step
        return "_orchestrator"

    # Single pass (F4)
    step_costs = {}
    total_cost = 0.0
    turn_count = 0
    line_num = 0
    with open(jsonl_path) as f:
        for raw_line in f:
            line_num += 1
            try: obj = json.loads(raw_line.strip())
            except json.JSONDecodeError: continue
            cost = compute_line_cost(obj)
            if cost > 0:
                step = find_step(line_num)
                step_costs[step] = step_costs.get(step, 0.0) + cost
                total_cost += cost
                turn_count += 1

    task_cost = max(0.0, total_cost - baseline_cost)
    scale = task_cost / total_cost if total_cost > 0 else 0.0
    step_actuals = {step: round(cost * scale, 4) for step, cost in step_costs.items()} if step_costs else None

    return {
        "total_session_cost": round(total_cost, 4),
        "actual_cost": round(task_cost, 4),
        "baseline_cost": round(baseline_cost, 4),
        "turn_count": turn_count,
        "step_actuals": step_actuals,
    }

  main() update (F7 — explicit sidecar check):
    jsonl_path = sys.argv[1]
    baseline_cost = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    sidecar_path = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    # calibration_dir is inferred from sidecar_path's parent directory
    # so agent-map.json is found in the same calibration/ dir as the sidecar

    if sidecar_path and Path(sidecar_path).exists():
        calibration_dir = str(Path(sidecar_path).parent)
        result = sum_session_by_agent(jsonl_path, sidecar_path, baseline_cost, calibration_dir)
    else:
        result = sum_session(jsonl_path, baseline_cost)
    print(json.dumps(result, indent=2))
```

### Change 3: scripts/update-factors.py — excluded field handling

**No staff review findings or enterprise constraints for this change.**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/update-factors.py
Lines: Pass 1 (read loop), approximately lines 119–152
Parallelism: independent
Description: Skip records with excluded=true during factor computation.
Details:
  - In Pass 1 read loop, after parsing each JSON record, before appending to all_records:
      if record.get('excluded', False):
          continue
  - No stderr message for excluded records (user-intentional, unlike outliers).
  - All existing tests pass (excluded defaults to False via .get()).
  - Truthiness note: string "true" is truthy — IS excluded. Consistent with F9. Comment added.
```

### Change 4: scripts/tokencostscope-learn.sh — sidecar discovery, true step ratios, review_cycles_actual

**Findings applied: F3, F6, F10**
**Enterprise constraint: E2 (storage writes routed through calibration_store.py)**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencostscope-learn.sh
Lines: VERSION bump (line 13), sidecar discovery block (after ~line 74), ACTUAL_JSON call
  (~line 83), eval of ACTUAL_JSON (~lines 88–96), RECORD Python block (~lines 102–160),
  history append and factor recompute (~lines 162–167), cleanup (~lines 169–172).
Parallelism: depends-on: [Change 1 (agent-hook.sh), Change 2 (sum-session-tokens.py),
  Change 13 (calibration_store.py)]
Description: Sidecar discovery, true step ratios, review_cycles_actual, optimistic/pessimistic
  in history (F10), session_expected naming (F3), cross-platform hash (F6). History append
  and factor recompute delegated to calibration_store.py (E2).
Details:
  - VERSION: "1.6.0" → "1.7.0"

  Sidecar discovery (after LATEST_JSONL resolved):
    SIDECAR_PATH=""
    find "$CALIBRATION_DIR" -name "*-timeline.jsonl" -mtime +7 -delete 2>/dev/null || true
    # Primary: hash same input as agent-hook.sh (F6 — cross-platform)
    HASH_INPUT="$CALIBRATION_DIR/active-estimate.json"
    if command -v md5 >/dev/null 2>&1; then
        SESSION_ID_HASH=$(printf '%s' "$HASH_INPUT" | md5 | cut -c1-12 2>/dev/null || echo "")
    elif command -v md5sum >/dev/null 2>&1; then
        SESSION_ID_HASH=$(printf '%s' "$HASH_INPUT" | md5sum | cut -c1-12 2>/dev/null || echo "")
    else
        SESSION_ID_HASH=""
    fi
    CANDIDATE="$CALIBRATION_DIR/${SESSION_ID_HASH}-timeline.jsonl"
    if [ -n "$SESSION_ID_HASH" ] && [ -f "$CANDIDATE" ]; then
        SIDECAR_PATH="$CANDIDATE"
    else
        SIDECAR_PATH=$(find "$CALIBRATION_DIR" -name "*-timeline.jsonl" -newer "$ESTIMATE_FILE" \
            -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -1 || echo "")
    fi

  ACTUAL_JSON computation:
    ACTUAL_JSON=$(python3 "$SCRIPT_DIR/sum-session-tokens.py" "$LATEST_JSONL" "$BASELINE_COST" \
        "${SIDECAR_PATH:-}" 2>/dev/null) || { rm -f "$ESTIMATE_FILE"; exit 0; }

  Extend eval of ACTUAL_JSON:
    eval "$(echo "$ACTUAL_JSON" | python3 -c "
    import sys, json, shlex
    d = json.load(sys.stdin)
    print(f'ACTUAL_COST={d.get(\"actual_cost\", 0)}')
    print(f'TURN_COUNT={d.get(\"turn_count\", 0)}')
    print(f'STEP_ACTUALS_JSON={shlex.quote(json.dumps(d.get(\"step_actuals\") or {}))}')
    ")" || { rm -f "$ESTIMATE_FILE"; exit 0; }

  RECORD Python block (F3 explicit naming, F10 optimistic/pessimistic, E2 storage delegation):
    Env vars: EC_ENV, AC_ENV, TC_ENV, SA_ENV, SIDECAR_PATH_ENV, plus all existing vars.

    In the Python block:
      step_actuals = json.loads(os.environ.get('SA_ENV', '{}')) or {}
      attribution_method = 'sidecar' if step_actuals else 'proportional'
      actual = float(os.environ['AC_ENV'])

      # F3: explicit name — session-level total, not per-step
      session_expected = max(float(os.environ['EC_ENV']), 0.001)

      if step_actuals and step_costs_estimated:
          step_ratios = {}
          for step_name, estimated in step_costs_estimated.items():
              actual_step = step_actuals.get(step_name, 0)
              if estimated > 0 and actual_step > 0:
                  step_ratios[step_name] = round(actual_step / estimated, 4)
      else:
          # Proportional fallback: session_expected is session-level (F3)
          session_ratio = round(actual / session_expected, 4)
          step_ratios = {step: session_ratio for step in step_costs_estimated}

      review_cycles_actual = None
      sidecar_path_env = os.environ.get('SIDECAR_PATH_ENV', '')
      if sidecar_path_env and os.path.exists(sidecar_path_env):
          sidecar_events = []
          with open(sidecar_path_env) as sf:
              for sline in sf:
                  try: sidecar_events.append(json.loads(sline))
                  except: pass
          rc_count = len([e for e in sidecar_events
              if e.get('type') == 'agent_stop'
              and 'staff' in e.get('agent_name', '').lower()
              and 'review' in e.get('agent_name', '').lower()])
          review_cycles_actual = rc_count if rc_count > 0 else None

      # F10: read from estimate file (already loaded as _est)
      optimistic_cost = _est.get('optimistic_cost', 0)
      pessimistic_cost = _est.get('pessimistic_cost', 0)
      ratio = round(actual / session_expected, 4)

      record_json = json.dumps({
          'timestamp': os.environ['TS_ENV'],
          'size': os.environ['SZ_ENV'],
          'files': int(os.environ['FL_ENV']),
          'complexity': os.environ['CX_ENV'],
          'expected_cost': float(os.environ['EC_ENV']),
          'optimistic_cost': optimistic_cost,
          'pessimistic_cost': pessimistic_cost,
          'actual_cost': actual,
          'ratio': ratio,
          'turn_count': int(os.environ['TC_ENV']),
          'steps': json.loads(os.environ['ST_ENV']),
          'pipeline_signature': os.environ['PIP_ENV'],
          'project_type': os.environ['PT_ENV'],
          'language': os.environ['LG_ENV'],
          'step_count': int(os.environ['SC_ENV']),
          'review_cycles_estimated': int(os.environ['RC_ENV']),
          'review_cycles_actual': review_cycles_actual,
          'parallel_groups': parallel_groups,
          'parallel_steps_detected': int(os.environ['PSD_ENV']),
          'file_brackets': _est.get('file_brackets'),
          'files_measured': _est.get('files_measured', 0),
          'step_costs_estimated': step_costs_estimated,
          'step_ratios': step_ratios,
          'step_actuals': step_actuals if step_actuals else None,
          'attribution_method': attribution_method,
      })
      print(record_json)

  History append and factor recompute (E2 — delegated to calibration_store.py):
    Replace the existing direct append + update-factors.py call with:
      # E2: route through storage helper — future enterprise adapter replaces this module
      python3 "$SCRIPT_DIR/calibration_store.py" append-history \
          --history "$HISTORY_FILE" \
          --factors "$FACTORS_FILE" \
          --record "$RECORD" 2>/dev/null || true
    calibration_store.py append-history writes the record to history.jsonl and
    calls update-factors.py. This concentrates the two storage operations.

  Sidecar cleanup:
    if [ -n "$SIDECAR_PATH" ] && [ -f "$SIDECAR_PATH" ]; then
        rm -f "$SIDECAR_PATH"
        COUNTER_FILE="${SIDECAR_PATH%-timeline.jsonl}-span-counter"
        rm -f "$COUNTER_FILE"
    fi
```

### Change 5: .claude/settings.json — hook registration

**Finding applied: F12 — separate matcher entries per hook**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/settings.json
Lines: all (full file rewrite)
Parallelism: depends-on: [Change 1 (agent-hook.sh)]
Description: Separate PostToolUse matcher entries for each hook (F12 — independent stdin pipes).
Details:

  {
    "hooks": {
      "Stop": [
        { "hooks": [{ "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencostscope-learn.sh'" }] }
      ],
      "PostToolUse": [
        { "matcher": "Agent", "hooks": [{ "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencostscope-track.sh'" }] },
        { "matcher": "Agent", "hooks": [{ "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencostscope-agent-hook.sh'" }] }
      ],
      "PreToolUse": [
        { "hooks": [{ "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencostscope-midcheck.sh'" }] },
        { "matcher": "Agent", "hooks": [{ "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencostscope-agent-hook.sh'" }] }
      ]
    }
  }

  agent-hook.sh self-filters on tool_name — safe even if PreToolUse matcher is unsupported.
```

### Change 6: Version bump in SKILL.md

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/SKILL.md
Lines: line 3 (frontmatter), line 386 (output template header)
Parallelism: depends-on: [Changes 1–5, 12, 13 complete]
Description: 1.6.0 → 1.7.0 in two places.
```

---

## Enterprise Changes (v1.7)

### Change 12: calibration/agent-map.json — configurable agent-to-step mapping

**Enterprise constraint: E1**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/calibration/agent-map.json
Lines: new file (gitignored — same lifecycle as other calibration data)
Parallelism: independent
Description: Optional per-project override for the agent-name → step-name mapping.
  When present, merged with DEFAULT_AGENT_TO_STEP in sum-session-tokens.py at runtime.
  Config file keys override defaults; default keys not in config are preserved.
  Enterprise teams populate this with their org-specific agent names.
Details:
  - File format: flat JSON object mapping agent_name (string) → step_name (string)
    Keys are lowercased and stripped at load time, so "Researcher" and "researcher" are equivalent.
  - Example content for a team using non-standard names:
      {
          "impl-backend": "Implementation",
          "impl-frontend": "Implementation",
          "sec-review": "Staff Review",
          "platform-qa": "QA"
      }
  - Absent file = use DEFAULT_AGENT_TO_STEP only (no error, no warning)
  - Malformed JSON = use DEFAULT_AGENT_TO_STEP (fail-open, no error)
  - The file is gitignored. To ship a project-specific default, teams may add it to their
    own .gitignore exclusion or check it in if the mapping is not sensitive.
  - Schema: plain JSON object (not JSONL, not nested). No schema_version needed — the
    format is trivial and forward-compatible by nature (new keys are simply ignored if
    the agent name never appears in sessions).
  - The calibration/ directory is already gitignored. Add a note to .gitignore comments
    documenting that agent-map.json lives here and can be committed if desired.
  - Do NOT create this file as part of implementation. It is user-created. Implementation
    only ensures _load_agent_map() handles its presence or absence gracefully.
```

### Change 13: scripts/calibration_store.py — storage abstraction helper

**Enterprise constraint: E2**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/calibration_store.py
Lines: new file (~80 lines)
Parallelism: independent
Description: Thin Python module concentrating all calibration storage reads and writes.
  Current implementation: local disk (same behavior as today's inline code).
  Future enterprise adapter: replace the disk I/O functions with remote API calls.
  This is NOT a full abstraction layer — it is a single file that gathers scattered
  storage interactions so a future swap touches one file, not many.
Details:

  Design principle (E2): The goal is concentration, not abstraction. Do not add
  abstract base classes, protocols, or dependency injection. A future implementer
  replacing local disk with a remote API will edit this file and only this file.
  All callers (learn.sh, tokencostscope-status.py) remain unchanged.

  Module-level:
    import json, os, sys, tempfile
    from pathlib import Path

  Functions:

  read_history(history_path: str) -> list[dict]:
    """Read all records from history.jsonl. Skip malformed lines.
    Returns empty list if file absent.
    """
    records = []
    if not Path(history_path).exists():
        return records
    with open(history_path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: records.append(json.loads(line))
            except json.JSONDecodeError: continue
    return records

  append_history(history_path: str, record: dict) -> None:
    """Append one record to history.jsonl. Creates file and parent dirs if absent."""
    os.makedirs(os.path.dirname(history_path) or ".", exist_ok=True)
    with open(history_path, "a") as f:
        f.write(json.dumps(record) + "\n")

  read_factors(factors_path: str) -> dict:
    """Read factors.json. Returns {} if absent or malformed."""
    if not Path(factors_path).exists():
        return {}
    try:
        with open(factors_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

  write_factors(factors_path: str, factors: dict) -> None:
    """Write factors.json atomically via temp file + rename."""
    dir_path = os.path.dirname(factors_path) or "."
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(factors, f, indent=2)
            f.write("\n")
        os.replace(tmp, factors_path)
    except Exception:
        os.unlink(tmp)
        raise

  CLI entry point (used by learn.sh to delegate storage operations):
    if __name__ == "__main__":
        cmd = sys.argv[1] if len(sys.argv) > 1 else ""

        if cmd == "append-history":
            # Args: --history PATH --factors PATH --record JSON_STRING
            # Parses args, appends record, then recomputes factors via update-factors.py.
            import argparse, subprocess
            parser = argparse.ArgumentParser()
            parser.add_argument("--history", required=True)
            parser.add_argument("--factors", required=True)
            parser.add_argument("--record", required=True)
            args = parser.parse_args(sys.argv[2:])
            record = json.loads(args.record)
            append_history(args.history, record)
            # Recompute factors (update-factors.py already handles atomic write)
            script_dir = os.path.dirname(os.path.abspath(__file__))
            subprocess.run(
                [sys.executable,
                 os.path.join(script_dir, "update-factors.py"),
                 args.history,
                 args.factors],
                check=False  # non-fatal if factor computation fails
            )

        elif cmd == "read-history":
            # Args: --history PATH [--json]
            # Reads and prints all history records as JSON array.
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("--history", required=True)
            args = parser.parse_args(sys.argv[2:])
            print(json.dumps(read_history(args.history), indent=2))

        else:
            print(f"Unknown command: {cmd}", file=sys.stderr)
            sys.exit(1)

  Note on tokencostscope-status.py (E2):
    Change 9 (status.py) must use calibration_store.read_history() and
    calibration_store.read_factors() instead of inline file reads.
    Import pattern (since calibration_store.py is in scripts/ and status.py is in scripts/):
      import importlib.util, os
      _store_path = os.path.join(os.path.dirname(__file__), "calibration_store.py")
      _spec = importlib.util.spec_from_file_location("calibration_store", _store_path)
      calibration_store = importlib.util.module_from_spec(_spec)
      _spec.loader.exec_module(calibration_store)
    Then use:
      records = calibration_store.read_history(args.history)
      factors = calibration_store.read_factors(args.factors)
    This keeps status.py's storage interactions concentrated in calibration_store.py.
```

---

## v1.7 Test Files

### Change 7: tests/test_agent_hook.py

**Findings applied: F1, F2, F13, F15**
**Enterprise constraint: E1 (test configurable mapping)**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_agent_hook.py
Lines: new file (~310 lines)
Parallelism: independent
Description: Unit and integration tests for agent-hook.sh and sum_session_by_agent().
  Covers schema (no parent_agent F1, span_id F2), configurable mapping (E1),
  SIDECAR_PATH_ENV in integration env (F13), zero-width span (F15).
Details:
  Test classes:

  TestSidecarEventSchema:
  - test_agent_start_event_fields: required fields: schema_version, type, timestamp,
    agent_name, session_id, jsonl_line_count, span_id, metadata
  - test_agent_stop_event_fields: same field set
  - test_schema_version_is_1: schema_version == 1
  - test_agent_name_lowercased: lowercased and stripped
  - test_no_parent_agent_field: F1 — "parent_agent" NOT in event dict
  - test_span_id_is_integer: F2 — span_id is int
  - test_span_id_increments: F2 — successive events have increasing span_id
  - test_metadata_is_empty_dict: metadata == {}

  TestAgentToStepMapping:
  - test_default_known_names_map: researcher → "Research Agent", etc.
  - test_engineer_ordinal_first: first "engineer" span → "Engineer Initial Plan"
  - test_engineer_ordinal_second: second → "Engineer Final Plan"
  - test_engineer_ordinal_third: F8 — third → raw agent name
  - test_engineer_initial_explicit: "engineer-initial" → "Engineer Initial Plan" via default map
  - test_engineer_final_explicit: "engineer-final" → "Engineer Final Plan" via default map
  - test_unrecognized_agent_raw_name: unknown name → stored as-is
  - test_custom_map_overrides_default: E1 — _load_agent_map with agent-map.json containing
    {"impl-backend": "Implementation"} → "impl-backend" maps to "Implementation"
  - test_custom_map_merges_with_defaults: E1 — custom key added, all default keys still work
  - test_missing_agent_map_uses_defaults: E1 — absent agent-map.json → DEFAULT_AGENT_TO_STEP
  - test_malformed_agent_map_uses_defaults: E1 — invalid JSON in agent-map.json → defaults

  TestNestingInference: (F1)
  - test_no_nesting_single_agent
  - test_nested_agent_inferred_from_open_span: B starts while A open → B is child of A
  - test_nested_agent_cost_subtracted_from_parent
  - test_deeply_nested_three_levels
  - test_parallel_non_overlapping_agents: no open span at second start → not nested

  TestFIFOSpanMatching: (F2)
  - test_fifo_two_sequential_same_agent
  - test_fifo_span_id_ordering
  - test_unmatched_stop_discarded
  - test_unmatched_start_gets_session_end

  TestSumSessionByAgent:
  - test_no_sidecar_returns_session_totals_only
  - test_missing_sidecar_returns_session_totals_only
  - test_single_agent_span_full_session
  - test_two_non_overlapping_agents
  - test_nested_agent_cost_not_double_counted
  - test_zero_width_span_no_cost: F15 — start_line == end_line → $0.00 for that span
  - test_unattributed_lines_in_orchestrator
  - test_empty_sidecar_no_step_actuals
  - test_malformed_sidecar_lines_skipped
  - test_unknown_schema_version_skipped
  - test_step_actuals_sum_to_actual_cost

  TestComputeLineCost: (F4)
  - test_non_assistant_type_zero
  - test_missing_usage_zero
  - test_synthetic_model_zero
  - test_known_model_correct_cost
  - test_unknown_model_falls_back_to_default
  - test_cost_includes_all_token_types

  TestLearnShAgentHookIntegration (skip if learn.sh absent):
    All tests pass SIDECAR_PATH_ENV in env dict (F13):
      env = {**os.environ,
             "TOKENCOSTSCOPE_ESTIMATE_FILE": str(estimate_file),
             "TOKENCOSTSCOPE_HISTORY_FILE": str(history_file),
             "SIDECAR_PATH_ENV": str(sidecar_file)}
  - test_step_actuals_written_to_history
  - test_attribution_method_sidecar
  - test_attribution_method_proportional_fallback
  - test_review_cycles_actual_populated
  - test_optimistic_pessimistic_in_history: F10
  - test_sidecar_deleted_after_processing
  - test_span_counter_deleted_after_processing
  - test_orphan_sidecar_swept_on_next_run

  TestAgentHookShellScript (skip if script absent):
  - test_non_agent_tool_exits_early
  - test_pre_tool_use_writes_agent_start
  - test_post_tool_use_writes_agent_stop
  - test_hook_is_fail_silent
  - test_sidecar_file_path_uses_session_id
  - test_span_id_in_event: F2
  - test_no_parent_agent_in_event: F1
```

### Change 8: tests/test_update_factors_excluded.py

**Finding applied: F9**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_update_factors_excluded.py
Lines: new file (~80 lines)
Parallelism: independent
Details:
  - test_excluded_true_boolean_not_counted
  - test_excluded_false_boolean_counted
  - test_missing_excluded_field_included
  - test_all_excluded_results_in_collecting
  - test_excluded_does_not_affect_outlier_count
  - test_mix_excluded_and_clean_records: 3 clean + 1 excluded → sample_count 3, status active
  - test_excluded_true_string_is_excluded: F9 — string "true" IS excluded (truthy).
    Assert sample_count does NOT include it. Comment: Python bool("true") = True, truthy.
    Users should use JSON boolean true, not string.
  - test_excluded_zero_not_excluded: excluded=0 → included (falsy)
```

---

## v2.0 Changes

### Change 9: scripts/tokencostscope-status.py

**Findings applied: F10, F11, F14**
**Enterprise constraint: E2 (reads via calibration_store), E3 (JSON output schema contract)**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencostscope-status.py
Lines: new file (~540–640 lines)
Parallelism: independent (after v1.7 merged)
Description: Pure-computation analysis engine. All storage reads via calibration_store (E2).
  JSON output schema treated as API contract (E3). Stored ratio used (F14). Stored
  optimistic/pessimistic for band_hit (F10). rec_session_outlier per-record (F11).
Details:

  E2 — Storage reads: load records via calibration_store.read_history(),
  factors via calibration_store.read_factors(). Import calibration_store via
  importlib.util (both files in scripts/). Do NOT use inline open() calls for
  history.jsonl or factors.json — all disk I/O goes through calibration_store.

  E3 — Schema contract: add comment block at top of the JSON output construction in analyze():
    # JSON OUTPUT SCHEMA CONTRACT (E3):
    # schema_version=1 is a versioned API contract. Downstream consumers may parse this output.
    # Fields may be ADDED to the v1 output without bumping schema_version (additive-only).
    # Removing or renaming fields, or changing field types, requires bumping schema_version to 2
    # and documenting a migration path. Do not break v1 consumers silently.

  Module-level constants: (same as previous version of this section)
    OUTLIER_HIGH, OUTLIER_LOW, DEFAULT_WINDOW_SESSIONS, DEFAULT_WINDOW_DAYS,
    STALE_PRICING_DAYS, and all recommendation thresholds.

  Functions (all unchanged from previous plan except storage calls):

  load_history(path) -> list:
    # E2: delegate to calibration_store
    return calibration_store.read_history(path)

  load_factors(path) -> dict:
    # E2: delegate to calibration_store
    return calibration_store.read_factors(path)

  is_outlier(record) -> bool:
    # F14: use stored ratio
    ratio = record.get('ratio') or (record.get('actual_cost', 0) /
            max(record.get('expected_cost', 0.001), 0.001))
    return ratio > OUTLIER_HIGH or ratio < OUTLIER_LOW

  get_ratio(record) -> float:
    # F14: use stored ratio field when present
    ratio = record.get('ratio')
    if ratio is not None: return float(ratio)
    return record.get('actual_cost', 0) / max(record.get('expected_cost', 0.001), 0.001)

  band_hit(record) -> str:
    # F10: use stored optimistic_cost / pessimistic_cost when available
    actual = record.get('actual_cost', 0)
    opt_cost = record.get('optimistic_cost')
    pess_cost = record.get('pessimistic_cost')
    if opt_cost is not None and actual <= opt_cost: return 'optimistic'
    if pess_cost is not None and actual <= pess_cost: return 'expected'
    if pess_cost is not None and actual > pess_cost: return 'over_pessimistic'
    # Fallback: ratio-based
    r = get_ratio(record)
    if r <= 0.6: return 'optimistic'
    if r <= 3.0: return 'expected'
    return 'over_pessimistic'

  rec_session_outlier(record) -> dict or None:
    # F11: takes single record, called per-record by compute_recommendations()
    if record.get('excluded', False): return None
    if not is_outlier(record): return None
    # ... returns recommendation dict

  compute_recommendations(windowed_records, all_records, factors, heuristics_path,
                           review_cycles_default):
    # F11: iterate windowed_records and call rec_session_outlier once per record
    for record in windowed_records:
        r = rec_session_outlier(record)
        if r is not None: recs.append(r)

  analyze(args) -> dict:
    all_records = load_history(args.history)   # E2: via calibration_store
    factors = load_factors(args.factors)       # E2: via calibration_store
    # ... sparse handling, window resolution, section computation
    result = {
        'schema_version': 1,    # E3: contract — additive-only for v1
        ...all sections...
    }
    return result

  All other functions (parse_args, resolve_window, compute_health, compute_accuracy,
  compute_cost_attribution, compute_outliers, compute_recommendations, individual rec_*
  functions, parse_review_cycles_default, parse_heuristics_pricing_date, _not_enough_data,
  main): unchanged from the prior version of this plan. See Change 9 in the pre-enterprise
  plan for full function-level pseudocode.
```

### Change 10: SKILL.md — status invocation mode

**No changes from pre-enterprise plan.**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/SKILL.md
Lines: activation section, new Status Mode section, version bump
Parallelism: depends-on: [Change 9]
Description: Status invocation mode. Version 1.7.0 → 2.0.0.
Details: (identical to pre-enterprise final plan — no enterprise changes affect SKILL.md)
  - Add "status" trigger to "When This Skill Activates"
  - Add "Do NOT activate" bullet for post-status re-triggering
  - New ## Status Mode section: parse flags, run status.py, format 5 sections,
    Apply actions (edit_heuristic, exclude_session, reset_calibration)
  - Version bump: 1.7.0 → 2.0.0 in frontmatter (line 3) and output template header (line 386)
```

---

## v2.0 Test Files

### Change 11: tests/test_status_analysis.py

**No changes from pre-enterprise plan for the test file itself.**
**Enterprise note: tests use calibration_store via status.py; no additional mocking needed
since calibration_store.read_history / read_factors fall back gracefully on absent files.**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_status_analysis.py
Lines: new file (~380–420 lines)
Parallelism: independent
Details: (identical to pre-enterprise final plan)
  TestGetRatio, TestBandHit, TestHealthComputation, TestAccuracyComputation,
  TestCostAttribution, TestOutlierReport, TestRecommendations, TestWindowResolution,
  TestSparseBehavior, TestJsonOutput, TestStatusScriptIntegration —
  all test cases as specified in the pre-enterprise final plan.
  Key tests for enterprise constraints are already covered:
  - test_json_schema_version: verifies schema_version=1 present (E3)
  - test_within_optimistic_uses_stored: verifies F10 band_hit with stored costs
  - test_rec_session_outlier_per_record: verifies F11 per-record calling pattern
  - test_uses_stored_ratio_field: verifies F14 get_ratio behavior
```

### Change 14: tests/test_calibration_store.py

**Enterprise constraint: E2 — tests for new storage module**

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_calibration_store.py
Lines: new file (~100 lines)
Parallelism: independent
Description: Unit tests for calibration_store.py storage operations.
Details:
  Test class: TestCalibrationStore

  Helpers:
    make_record(ratio=1.0) -> dict: minimal valid history record
    make_history_file(tmp_path, records) -> str: writes JSONL, returns path

  Tests:

  read_history:
  - test_read_history_absent_file: missing path → empty list (no exception)
  - test_read_history_empty_file: empty file → empty list
  - test_read_history_parses_records: 3 records → list of 3 dicts
  - test_read_history_skips_malformed_lines: bad JSON line skipped, others parsed
  - test_read_history_skips_blank_lines: blank lines produce no records

  append_history:
  - test_append_creates_file: absent file → creates file and parent dirs
  - test_append_adds_record: existing file + new record → file has both
  - test_append_writes_valid_json: appended line is parseable JSON

  read_factors:
  - test_read_factors_absent_file: missing path → empty dict
  - test_read_factors_malformed_json: bad JSON → empty dict (no exception)
  - test_read_factors_valid: valid factors.json → correct dict returned

  write_factors:
  - test_write_factors_atomic: writes via temp file; if interrupted, old file intact
  - test_write_factors_creates_dirs: parent dirs created if absent
  - test_write_factors_valid_json: output is parseable JSON

  CLI (append-history command):
  - test_cli_append_history: run via subprocess with --history, --factors, --record;
    verify record appears in history file after run
  - test_cli_append_history_triggers_factor_recompute: after append with 3+ records,
    factors.json exists (update-factors.py was called)
```

---

## Dependency Order

### v1.7 Execution Order

**Parallel batch 1** (no dependencies, can run concurrently):
- Change 1: tokencostscope-agent-hook.sh (new file)
- Change 2: sum-session-tokens.py (compute_line_cost, _load_agent_map, sum_session_by_agent, _build_spans)
- Change 3: update-factors.py (excluded field in Pass 1)
- Change 12: calibration/agent-map.json (documentation only; no file created by implementer)
- Change 13: scripts/calibration_store.py (new file, no dependencies)
- Change 7: tests/test_agent_hook.py (write in parallel — interface defined)
- Change 8: tests/test_update_factors_excluded.py
- Change 14: tests/test_calibration_store.py (write in parallel with Change 13)

**Sequential after batch 1**:
- Change 4: tokencostscope-learn.sh (depends on Changes 1, 2, 13)
- Change 5: .claude/settings.json (depends on Change 1)

**Sequential after Changes 4 and 5**:
- Change 6: SKILL.md version bump (depends on Changes 1–5, 12, 13 complete)

### v2.0 Execution Order

**After v1.7 is merged:**

**Parallel batch 2**:
- Change 9: tokencostscope-status.py (depends on Change 13 already merged)
- Change 11: tests/test_status_analysis.py (write in parallel — interface fully specified)

**Sequential after batch 2**:
- Change 10: SKILL.md status invocation mode + version 2.0.0 (depends on Change 9)

---

## Test Strategy

### New test files
- `tests/test_agent_hook.py` — agent-hook.sh + sum_session_by_agent() + configurable mapping
- `tests/test_update_factors_excluded.py` — excluded field in update-factors.py
- `tests/test_calibration_store.py` — calibration_store.py storage operations (E2)
- `tests/test_status_analysis.py` — tokencostscope-status.py analysis and JSON output

### Existing tests that may need updating
- `tests/test_per_step_factors.py`: `TestLearnShIntegrationStepCosts` tests learn.sh step_ratios.
  learn.sh now delegates history append to calibration_store.py — ensure the integration test
  either (a) uses TOKENCOSTSCOPE_HISTORY_FILE env var (which calibration_store still respects via
  the --history arg passed by learn.sh), or (b) provides a writable tmp dir. SIDECAR_PATH_ENV
  must NOT be set in these tests (proportional fallback path).
- `tests/test_parallel_agent_accounting.py`: no changes expected.
- All suites: run `/usr/bin/python3 -m pytest tests/ -v` after each change group.

### tokens_by_model removal note
Change 2 removes `tokens_by_model` from `sum_session()` return dict (F4 consequence).
Before implementation: `grep -r 'tokens_by_model' tests/` — remove any assertions found.

### Edge cases to cover
- Hook on non-Agent tool: exits 0, no sidecar
- Empty sidecar: step_actuals = None
- Malformed sidecar lines: skipped gracefully
- Nested agents: child range subtracted from parent (F1)
- Zero-width span: $0.00 attributed (F15)
- FIFO: second same-name agent matched to second start (F2)
- 3+ engineer spans: third gets raw name (F8)
- No sidecar: proportional attribution (F3)
- session_expected vs step-level (F3)
- Stored optimistic/pessimistic: band_hit uses them (F10)
- rec_session_outlier per record: 2 outliers → 2 recs (F11)
- Stored ratio: get_ratio returns it (F14)
- Custom agent-map.json: overrides default, defaults preserved (E1)
- Absent agent-map.json: DEFAULT_AGENT_TO_STEP used (E1)
- calibration_store absent file reads: empty list/dict, no exception (E2)
- Schema_version != 1 sidecar events: skipped (E3)

### Test runner
All tests: `/usr/bin/python3 -m pytest tests/` (system Python 3.9 with pytest).
Do NOT use `pytest` or `python3 -m pytest` directly (Homebrew Python 3.14 lacks pytest).

---

## Rollback Notes

### v1.7 rollback
- `tokencostscope-agent-hook.sh`: delete. Fail-silent — no existing functionality breaks.
- `settings.json`: remove new hook entries.
- `sum-session-tokens.py`: `git checkout`. tokens_by_model removal is the only breaking change
  for callers (none use it; verify before reverting).
- `tokencostscope-learn.sh`: `git checkout`. storage delegation to calibration_store.py is
  the only structural change; if calibration_store.py is also reverted, learn.sh reverts to
  inline append + update-factors.py call.
- `calibration_store.py`: delete. learn.sh reverted above no longer calls it.
- `update-factors.py`: revert single excluded skip line.
- `SKILL.md`: revert version bump (two lines).
- `calibration/agent-map.json`: user-created, user-managed. No action needed on rollback.
- Data: sidecar files are transient. history.jsonl gains new fields (backward compatible).

### v2.0 rollback
- `tokencostscope-status.py`: delete.
- `SKILL.md`: revert status mode section and version bump.
- `history.jsonl` excluded records: backward compatible; old update-factors.py ignores unknown fields.
- `calibration_store.py`: retain (it is part of v1.7, not v2.0).
- reset_calibration is user-initiated — no automated rollback.
