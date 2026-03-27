#!/usr/bin/env bash
# tokencast-learn.sh — Stop hook for automatic learning
#
# Fires when a Claude Code session ends. Reads the session's JSONL log,
# computes actual token cost, compares to the active estimate (if any),
# and appends to calibration history. Then recomputes calibration factors.
#
# This script is designed to fail silently — learning is best-effort.
# A failed learning run does not affect the user's workflow.

set -euo pipefail

VERSION="2.1.0"

if [ "${1:-}" = "--version" ]; then
    echo "tokencast $VERSION"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
CALIBRATION_DIR="$SKILL_DIR/calibration"
ESTIMATE_FILE="${TOKENCOSTSCOPE_ESTIMATE_FILE:-$CALIBRATION_DIR/active-estimate.json}"
HISTORY_FILE="${TOKENCOSTSCOPE_HISTORY_FILE:-$CALIBRATION_DIR/history.jsonl}"
FACTORS_FILE="$CALIBRATION_DIR/factors.json"

# Exit early if no active estimate was recorded this session.
# Reconstitution fallback: if last-estimate.md is recent (< 48h), rebuild the
# estimate from it so continuation sessions produce calibration records.
if [ ! -f "$ESTIMATE_FILE" ]; then
    LAST_ESTIMATE_MD="$(dirname "$ESTIMATE_FILE")/last-estimate.md"
    if [ -f "$LAST_ESTIMATE_MD" ]; then
        # TOKENCOSTSCOPE_CONTINUATION_MAX_AGE_HOURS (if set) is inherited by
        # parse_last_estimate.py from the environment — no explicit forwarding needed.
        python3 "$SCRIPT_DIR/parse_last_estimate.py" "$LAST_ESTIMATE_MD" > "$ESTIMATE_FILE" 2>/dev/null || {
            rm -f "$ESTIMATE_FILE"
            exit 0
        }
        # Guard against empty output (parse_last_estimate.py exited 0 but wrote nothing)
        if [ ! -s "$ESTIMATE_FILE" ]; then
            rm -f "$ESTIMATE_FILE"
            exit 0
        fi
        # Backdate the reconstituted file to last-estimate.md's mtime so the
        # -newer "$ESTIMATE_FILE" JSONL discovery below correctly identifies
        # this session's JSONL (which was written after last-estimate.md, not
        # after the reconstituted file which was just written "now").
        # Assumption: last-estimate.md was written during the original estimate
        # session — not modified by any intermediate session between then and now.
        touch -r "$LAST_ESTIMATE_MD" "$ESTIMATE_FILE" 2>/dev/null || true
    else
        exit 0
    fi
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
    # Use find -exec ls instead of find|xargs — GNU xargs (Linux) runs `ls`
    # with no arguments on empty stdin, listing cwd (cross-platform bug).
    # Note: -exec ... + may batch if file count exceeds ARG_MAX, so head -1
    # is not guaranteed globally newest in extreme cases. Acceptable for
    # typical session counts (< 100 JSONL files).
    if [ -d "$HOME/.claude/projects/" ]; then
        LATEST_JSONL=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f -newer "$ESTIMATE_FILE" \
            -exec ls -t {} + 2>/dev/null | head -1)

        if [ -z "$LATEST_JSONL" ]; then
            # Fallback: find the most recently modified JSONL anywhere
            LATEST_JSONL=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f \
                -exec ls -t {} + 2>/dev/null | head -1)
        fi
    fi
fi

if [ -z "$LATEST_JSONL" ] || [ ! -f "$LATEST_JSONL" ]; then
    # Can't find session log — clean up and exit
    rm -f "$ESTIMATE_FILE"
    exit 0
fi

# Sweep stale sidecar files (older than 7 days) before discovering current one
if [ -d "$CALIBRATION_DIR" ]; then
    find "$CALIBRATION_DIR" -name "*-timeline.jsonl" -mtime +7 -delete 2>/dev/null || true
fi

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
        # Fallback: find most recently modified timeline newer than the estimate.
        # Use find -exec ls instead of find|xargs — GNU xargs runs `ls` with no
        # arguments on empty stdin, listing cwd (cross-platform bug).
        if [ -d "$CALIBRATION_DIR" ]; then
            SIDECAR_PATH=$(find "$CALIBRATION_DIR" -name "*-timeline.jsonl" -newer "$ESTIMATE_FILE" \
                -exec ls -t {} + 2>/dev/null | head -1 || echo "")
        fi
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
    # Count review cycles from sidecar before building the record
    REVIEW_CYCLES_ACTUAL=""
    if [ -n "${SIDECAR_PATH:-}" ] && [ -f "$SIDECAR_PATH" ]; then
        REVIEW_CYCLES_ACTUAL=$(SIDECAR_PATH_ENV="$SIDECAR_PATH" python3 -c "
import json, os
sidecar_path = os.environ['SIDECAR_PATH_ENV']
events = []
with open(sidecar_path) as sf:
    for line in sf:
        try: events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError): pass
rc = len([e for e in events
    if e.get('type') == 'agent_stop'
    and 'staff' in e.get('agent_name', '').lower()
    and 'review' in e.get('agent_name', '').lower()])
print(rc if rc > 0 else '')
" 2>/dev/null || echo "")
    fi

    # Create history record via build_history_record() — all values passed via env vars
    RECORD=$(
      SCRIPT_DIR_ENV="$SCRIPT_DIR" \
      AC_ENV="$ACTUAL_COST" TC_ENV="$TURN_COUNT" \
      SA_ENV="$STEP_ACTUALS_JSON" \
      RC_ACT_ENV="${REVIEW_CYCLES_ACTUAL:-}" \
      EST_FILE="$ESTIMATE_FILE" \
      python3 -c "
import json, os, sys, importlib.util
script_dir = os.environ['SCRIPT_DIR_ENV']
# Import session_recorder directly via importlib to avoid triggering
# tokencast/__init__.py which has heavy transitive imports (api.py, mcp, etc.)
_sr_path = os.path.join(os.path.dirname(script_dir), 'src', 'tokencast', 'session_recorder.py')
_spec = importlib.util.spec_from_file_location('session_recorder', _sr_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_history_record = _mod.build_history_record

# open() is intentionally bare here — this is short-lived inline Python in a shell script; the process exits immediately after print().
_est = json.load(open(os.environ['EST_FILE'])) if os.path.exists(os.environ.get('EST_FILE', '')) else {}

step_actuals_raw = json.loads(os.environ.get('SA_ENV', '{}')) or {}
step_actuals_sidecar = step_actuals_raw if step_actuals_raw else None

rc_raw = os.environ.get('RC_ACT_ENV', '')
review_cycles_actual = int(rc_raw) if rc_raw.strip().isdigit() else None

record = build_history_record(
    estimate=_est,
    actual_cost=float(os.environ['AC_ENV']),
    turn_count=int(os.environ.get('TC_ENV', 0) or 0),
    review_cycles_actual=review_cycles_actual,
    step_actuals_sidecar=step_actuals_sidecar,
)
print(json.dumps(record))
"
    )

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
