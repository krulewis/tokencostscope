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

VERSION="1.6.0"

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

# Compute actual cost from session log
ACTUAL_JSON=$(python3 "$SCRIPT_DIR/sum-session-tokens.py" "$LATEST_JSONL" "$BASELINE_COST" 2>/dev/null) || {
    rm -f "$ESTIMATE_FILE"
    exit 0
}

eval "$(echo "$ACTUAL_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'ACTUAL_COST={d.get(\"actual_cost\", 0)}')
print(f'TURN_COUNT={d.get(\"turn_count\", 0)}')
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

actual = float(os.environ['AC_ENV'])
expected = max(float(os.environ['EC_ENV']), 0.001)

# Compute per-step ratios via proportional attribution.
# session_ratio uses the session-level actual/expected ratio — the SAME value
# is applied to ALL steps. This is intentional: we cannot measure per-step
# actual costs from session JSONL (no step-level tagging). The session-level
# ratio is the best available proxy. Do NOT divide by per-step expected costs
# here — that would defeat the proportional attribution design.
# 'expected' here is the session-level total expected cost (same variable
# used for the global factor computation above), not a per-step value.
session_ratio = round(actual / expected, 4)
step_ratios = {step: session_ratio for step in step_costs_estimated}

# step_costs_estimated is diagnostic only — stored in history for inspection
# and debugging. It is NOT used by update-factors.py for factor computation.
# Factor computation uses step_ratios exclusively.
print(json.dumps({
    'timestamp': os.environ['TS_ENV'],
    'size': os.environ['SZ_ENV'],
    'files': int(os.environ['FL_ENV']),
    'complexity': os.environ['CX_ENV'],
    'expected_cost': float(os.environ['EC_ENV']),
    'actual_cost': actual,
    'ratio': round(actual / expected, 4),
    'turn_count': int(os.environ['TC_ENV']),
    'steps': json.loads(os.environ['ST_ENV']),
    'pipeline_signature': os.environ['PIP_ENV'],
    'project_type': os.environ['PT_ENV'],
    'language': os.environ['LG_ENV'],
    'step_count': int(os.environ['SC_ENV']),
    'review_cycles_estimated': int(os.environ['RC_ENV']),
    'review_cycles_actual': None,
    'parallel_groups': parallel_groups,
    'parallel_steps_detected': int(os.environ['PSD_ENV']),
    'file_brackets': _est.get('file_brackets'),
    'files_measured': _est.get('files_measured', 0),
    'step_costs_estimated': step_costs_estimated,
    'step_ratios': step_ratios,
}))
")

    # Append to history
    mkdir -p "$CALIBRATION_DIR"
    echo "$RECORD" >> "$HISTORY_FILE"

    # Recompute calibration factors
    python3 "$SCRIPT_DIR/update-factors.py" "$HISTORY_FILE" "$FACTORS_FILE" 2>/dev/null || true
fi

# Clean up the active estimate marker
rm -f "$ESTIMATE_FILE"

exit 0
