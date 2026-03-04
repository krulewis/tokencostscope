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

VERSION="1.1.0"

if [ "${1:-}" = "--version" ]; then
    echo "tokencostscope $VERSION"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
CALIBRATION_DIR="$SKILL_DIR/calibration"
ESTIMATE_FILE="$CALIBRATION_DIR/active-estimate.json"
HISTORY_FILE="$CALIBRATION_DIR/history.jsonl"
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
    'EXPECTED_COST': d.get('expected_cost', 0),
    'SIZE': d.get('size', 'M'),
    'FILES': d.get('files', 0),
    'COMPLEXITY': d.get('complexity', 'medium'),
    'BASELINE_COST': d.get('baseline_cost', 0),
    'STEPS_JSON': json.dumps(steps),
    'PIPELINE_SIGNATURE': sig,
    'PROJECT_TYPE': d.get('project_type', 'unknown'),
    'LANGUAGE': d.get('language', 'unknown'),
    'STEP_COUNT': d.get('step_count', 0),
}
for k, v in fields.items():
    print(f'{k}={shlex.quote(str(v))}')
" 2>/dev/null)" || {
    rm -f "$ESTIMATE_FILE"
    exit 0
}

# Find the most recent session JSONL
# Search all project directories under ~/.claude/projects/
LATEST_JSONL=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f -newer "$ESTIMATE_FILE" -print0 2>/dev/null | \
    xargs -0 ls -t 2>/dev/null | head -1)

if [ -z "$LATEST_JSONL" ]; then
    # Fallback: find the most recently modified JSONL anywhere
    LATEST_JSONL=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f -print0 2>/dev/null | \
        xargs -0 ls -t 2>/dev/null | head -1)
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
      python3 -c "
import json, os
actual = float(os.environ['AC_ENV'])
expected = max(float(os.environ['EC_ENV']), 0.001)
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
