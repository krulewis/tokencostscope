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

# Read the active estimate
ESTIMATE=$(cat "$ESTIMATE_FILE")
EXPECTED_COST=$(echo "$ESTIMATE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('expected_cost', 0))")
SIZE=$(echo "$ESTIMATE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('size', 'M'))")
FILES=$(echo "$ESTIMATE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('files', 0))")
COMPLEXITY=$(echo "$ESTIMATE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('complexity', 'medium'))")
BASELINE_COST=$(echo "$ESTIMATE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('baseline_cost', 0))")

# Find the most recent session JSONL
# Search all project directories under ~/.claude/projects/
LATEST_JSONL=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f -newer "$ESTIMATE_FILE" 2>/dev/null | \
    xargs ls -t 2>/dev/null | head -1)

if [ -z "$LATEST_JSONL" ]; then
    # Fallback: find the most recently modified JSONL anywhere
    LATEST_JSONL=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f 2>/dev/null | \
        xargs ls -t 2>/dev/null | head -1)
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

ACTUAL_COST=$(echo "$ACTUAL_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('actual_cost', 0))")
TURN_COUNT=$(echo "$ACTUAL_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('turn_count', 0))")

# Skip if actual cost is zero or negative (session had no real work)
if python3 -c "exit(0 if float('$ACTUAL_COST') > 0.001 else 1)" 2>/dev/null; then
    # Compute ratio
    RATIO=$(python3 -c "print(round(float('$ACTUAL_COST') / max(float('$EXPECTED_COST'), 0.001), 4))")

    # Create history record
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    RECORD=$(python3 -c "
import json
print(json.dumps({
    'timestamp': '$TIMESTAMP',
    'size': '$SIZE',
    'files': int('$FILES'),
    'complexity': '$COMPLEXITY',
    'expected_cost': float('$EXPECTED_COST'),
    'actual_cost': float('$ACTUAL_COST'),
    'ratio': float('$RATIO'),
    'turn_count': int('$TURN_COUNT'),
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
