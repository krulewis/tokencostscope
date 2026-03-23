#!/usr/bin/env bash
# tokencostscope-learn.sh — Stop hook for automatic learning
#
# Fires when a Claude Code session ends. Reads the session's JSONL log,
# computes actual token cost, compares to the active estimate (if any),
# and appends to calibration history. Then recomputes calibration factors.
#
# This script is designed to fail silently — learning is best-effort.
# A failed learning run does not affect the user's workflow.

set -euo pipefail

VERSION="2.0.0"

if [ "${1:-}" = "--version" ]; then
    echo "tokencostscope $VERSION"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
CALIBRATION_DIR="$SKILL_DIR/calibration"
ESTIMATE_FILE="${TOKENCOSTSCOPE_ESTIMATE_FILE:-$CALIBRATION_DIR/active-estimate.json}"
HISTORY_FILE="${TOKENCOSTSCOPE_HISTORY_FILE:-$CALIBRATION_DIR/history.jsonl}"
FACTORS_FILE="$CALIBRATION_DIR/factors.json"

# Exit early if no active estimate was recorded this session
if [ ! -f "$ESTIMATE_FILE" ]; then
    exit 0
fi

# Read and parse the active estimate in a single Python call
eval "$(EST_FILE="$ESTIMATE_FILE" python3 -c "
import json, shlex, os
with open(os.environ['EST_FILE']) as f:
    d = json.load(f)
steps = d.get('steps', [])
sig = '+'.join(sorted(s.lower().replace(' ', '_') for s in steps))
fields = {
    'EXPECTED_COST': d.get('expected_cost') or 0,
    'SIZE': d.get('size') or 'M',
    'FILES': d.get('files') or 0,
    'COMPLEXITY': d.get('complexity') or 'medium',
    'BASELINE_COST': d.get('baseline_cost') or 0,
    'STEPS_JSON': json.dumps(steps),
    'PIPELINE_SIGNATURE': sig,
    'PROJECT_TYPE': d.get('project_type') or 'unknown',
    'LANGUAGE': d.get('language') or 'unknown',
    'STEP_COUNT': d.get('step_count') or 0,
    'REVIEW_CYCLES': d.get('review_cycles_estimated') or 0,
    'PARALLEL_STEPS_DETECTED': d.get('parallel_steps_detected') or 0,
}
for k, v in fields.items():
    print(f'{k}={shlex.quote(str(v))}')
" 2>/dev/null)" || {
    rm -f "$ESTIMATE_FILE"
    exit 0
}

# Find the most recent session JSONL
# If a path is provided as $1, use it directly (allows integration tests to inject a mock session)
if [ -n "${1:-}" ] && [ -f "$1" ]; then
    LATEST_JSONL="$1"
else
    # Search all project directories under ~/.claude/projects/
    LATEST_JSONL=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f -newer "$ESTIMATE_FILE" -print0 2>/dev/null | \
        xargs -0 ls -t 2>/dev/null | head -1)

    if [ -z "$LATEST_JSONL" ]; then
        # Fallback: find the most recently modified JSONL anywhere
        LATEST_JSONL=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f -print0 2>/dev/null | \
            xargs -0 ls -t 2>/dev/null | head -1)
    fi
fi

if [ -z "$LATEST_JSONL" ] || [ ! -f "$LATEST_JSONL" ]; then
    # Can't find session log — clean up and exit
    rm -f "$ESTIMATE_FILE"
    exit 0
fi

# Sweep stale sidecar files (older than 7 days) before discovering current one
find "$CALIBRATION_DIR" -name "*-timeline.jsonl" -mtime +7 -delete 2>/dev/null || true

# Sidecar discovery — match the session_id hash used by agent-hook.sh (F6: cross-platform)
# TOKENCOSTSCOPE_SIDECAR_PATH env var allows tests (and future callers) to inject a path directly.
SIDECAR_PATH="${TOKENCOSTSCOPE_SIDECAR_PATH:-}"
if [ -z "$SIDECAR_PATH" ]; then
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
        # Fallback: find most recently modified timeline newer than the estimate
        SIDECAR_PATH=$(find "$CALIBRATION_DIR" -name "*-timeline.jsonl" -newer "$ESTIMATE_FILE" \
            -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -1 || echo "")
    fi
fi

# Compute actual cost from session log — pass sidecar path if available (for step attribution)
ACTUAL_JSON=$(python3 "$SCRIPT_DIR/sum-session-tokens.py" "$LATEST_JSONL" "$BASELINE_COST" \
    "${SIDECAR_PATH:-}" 2>/dev/null) || {
    rm -f "$ESTIMATE_FILE"
    exit 0
}

eval "$(echo "$ACTUAL_JSON" | python3 -c "
import sys, json, shlex
d = json.load(sys.stdin)
print(f'ACTUAL_COST={d.get(\"actual_cost\", 0)}')
print(f'TURN_COUNT={d.get(\"turn_count\", 0)}')
print(f'STEP_ACTUALS_JSON={shlex.quote(json.dumps(d.get(\"step_actuals\") or {}))}')
")" || {
    rm -f "$ESTIMATE_FILE"
    exit 0
}

# Skip if actual cost is zero or negative (session had no real work)
if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) > 0.001 else 1)" "$ACTUAL_COST" 2>/dev/null; then
    # Create history record — all values passed via env vars to avoid injection
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    RECORD=$(TS_ENV="$TIMESTAMP" SZ_ENV="$SIZE" FL_ENV="$FILES" CX_ENV="$COMPLEXITY" \
      EC_ENV="$EXPECTED_COST" AC_ENV="$ACTUAL_COST" TC_ENV="$TURN_COUNT" \
      ST_ENV="$STEPS_JSON" PIP_ENV="$PIPELINE_SIGNATURE" \
      PT_ENV="$PROJECT_TYPE" LG_ENV="$LANGUAGE" SC_ENV="$STEP_COUNT" \
      RC_ENV="$REVIEW_CYCLES" \
      PSD_ENV="$PARALLEL_STEPS_DETECTED" EST_FILE="$ESTIMATE_FILE" \
      SA_ENV="$STEP_ACTUALS_JSON" SIDECAR_PATH_ENV="${SIDECAR_PATH:-}" \
      python3 -c "
import json, os
_est = json.load(open(os.environ['EST_FILE'])) if os.path.exists(os.environ.get('EST_FILE', '')) else {}
parallel_groups = _est.get('parallel_groups', [])
# Read step_costs from estimate; exclude PR Review Loop from per-step attribution.
# 'PR Review Loop' is matched by exact string (case-sensitive) — must match the
# exact key written by SKILL.md. Do not use prefix matching or case-folding.
step_costs_raw = _est.get('step_costs', {})
PR_REVIEW_LOOP_KEY = 'PR Review Loop'
step_costs_estimated = {k: v for k, v in step_costs_raw.items()
                        if k != PR_REVIEW_LOOP_KEY}

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

# F10: read optimistic/pessimistic from estimate file
optimistic_cost = _est.get('optimistic_cost', 0)
pessimistic_cost = _est.get('pessimistic_cost', 0)

ratio = round(actual / session_expected, 4)

# review_cycles_actual: count staff-reviewer agent_stop events in sidecar
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

print(json.dumps({
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
}))
")

    # E2: route storage through calibration_store.py — future enterprise adapter replaces this module
    mkdir -p "$CALIBRATION_DIR"
    python3 "$SCRIPT_DIR/calibration_store.py" append-history \
        --history "$HISTORY_FILE" \
        --factors "$FACTORS_FILE" \
        --record "$RECORD" 2>/dev/null || true
fi

# Clean up the active estimate marker
rm -f "$ESTIMATE_FILE"

# Clean up sidecar and span counter for this session
if [ -n "$SIDECAR_PATH" ] && [ -f "$SIDECAR_PATH" ]; then
    rm -f "$SIDECAR_PATH"
    COUNTER_FILE="${SIDECAR_PATH%-timeline.jsonl}-span-counter"
    rm -f "$COUNTER_FILE"
fi

exit 0
