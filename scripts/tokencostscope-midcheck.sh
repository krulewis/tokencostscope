#!/usr/bin/env bash
# tokencostscope-midcheck.sh — PreToolUse hook for mid-session cost tracking
#
# Fires before every tool call. Uses a file-size sampling gate so the
# expensive JSONL parse runs at most once per ~50KB of JSONL growth.
#
# Output: JSON to stdout with additionalContext when threshold exceeded.
#         Silent exit (no stdout, no stderr) when below threshold or not applicable.
#
# Fail-silent: set -euo pipefail + || exit 0 pattern ensures any unexpected
# error causes a silent exit. The || exit 0 on each fallible command short-
# circuits pipefail before it can surface an error to the hook runner.

set -euo pipefail
# NOTE: set -euo pipefail is intentional even though every command uses || exit 0.
# The combination means: any unguarded error exits immediately (pipefail), but all
# commands that are expected to fail use || exit 0 to turn errors into silent exits.
# Net effect: unexpected bugs exit silently rather than producing partial output.

# ---- OS detection for stat (done once at script top, not per-call) ----
# (finding 11: single detection block rather than per-call fallback)
if [[ "$(uname)" == "Darwin" ]]; then
    STAT_CMD="stat -f%z"
else
    STAT_CMD="stat -c%s"
fi

# ---- Path setup ----
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
CALIBRATION_DIR="$SKILL_DIR/calibration"
# (finding 12: env var override for testability, same pattern as learn.sh)
ESTIMATE_FILE="${TOKENCOSTSCOPE_ESTIMATE_FILE:-$CALIBRATION_DIR/active-estimate.json}"
STATE_FILE="${TOKENCOSTSCOPE_MIDCHECK_STATE_FILE:-$CALIBRATION_DIR/.midcheck-state}"

# ---- Guard: no active estimate ----
[ -f "$ESTIMATE_FILE" ] || exit 0

# ---- Read pessimistic and baseline cost from estimate ----
# bare python3 is acceptable here — only stdlib (json, os, sys) is used.
# This is the same convention as tokencostscope-learn.sh.
# (finding 5: explicit comment documenting bare python3 is acceptable)
PESSIMISTIC=$(EST_FILE="$ESTIMATE_FILE" python3 -c "
import json, os, sys
try:
    d = json.load(open(os.environ['EST_FILE']))
    pess = d.get('pessimistic_cost', 0)
    if not pess or pess <= 0:
        sys.exit(1)
    print(pess)
except Exception:
    sys.exit(1)
" 2>/dev/null) || exit 0

BASELINE=$(EST_FILE="$ESTIMATE_FILE" python3 -c "
import json, os, sys
try:
    d = json.load(open(os.environ['EST_FILE']))
    print(d.get('baseline_cost', 0) or 0)
except Exception:
    sys.exit(1)
" 2>/dev/null) || exit 0

# ---- Resolve JSONL path ----
# Prefer transcript_path from the PreToolUse stdin JSON payload.
# Fall back to finding the most recent JSONL if stdin is absent or invalid.
# (finding 3: timeout 1 cat prevents blocking on empty/slow stdin)
# (finding 4: STDIN_JSON passed via env var, not sys.argv, to avoid shell injection)
STDIN_JSON=$(timeout 1 cat 2>/dev/null || true)
JSONL_PATH=$(STDIN_ENV="$STDIN_JSON" python3 -c "
import os, json, sys
try:
    d = json.loads(os.environ.get('STDIN_ENV', ''))
    tp = d.get('transcript_path', '')
    if tp:
        print(tp)
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null) || {
    # Fallback: most recent JSONL in ~/.claude/projects/
    JSONL_PATH=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f -print0 2>/dev/null | \
        xargs -0 ls -t 2>/dev/null | head -1) || true
}

[ -n "$JSONL_PATH" ] && [ -f "$JSONL_PATH" ] || exit 0

# ---- Sampling gate: check file size vs last-checked offset ----
# STATE_FILE format: two lines
#   line 1: last_checked_size (bytes)
#   line 2: cooldown_sentinel ("0" = no cooldown, "COOLDOWN:<size>" = suppress until size)
#
# All three parameters below are tunable — see references/heuristics.md
# "Mid-Session Cost Tracking" section for documentation and rationale.
# (finding 10: inline heuristics.md reference comments for all 3 parameters)
MIDCHECK_SAMPLING_BYTES=50000   # heuristics.md: midcheck_sampling_bytes
MIDCHECK_COOLDOWN_BYTES=200000  # heuristics.md: midcheck_cooldown_bytes
MIDCHECK_WARN_THRESHOLD=0.80    # heuristics.md: midcheck_warn_threshold

CURRENT_SIZE=$($STAT_CMD "$JSONL_PATH" 2>/dev/null) || exit 0

# (finding 9: note potential race condition between stat and subsequent reads)
# Race condition: JSONL may be written between the stat call above and the parse
# below. This is benign — the extra bytes will be counted in the next cycle.

if [ ! -f "$STATE_FILE" ]; then
    # First run: record current size, exit without warning.
    # (finding 9: state file write is also subject to race; use || true to stay fail-silent)
    printf '%s\n%s\n' "$CURRENT_SIZE" "0" > "$STATE_FILE" 2>/dev/null || true
    exit 0
fi

LAST_SIZE=$(sed -n '1p' "$STATE_FILE" 2>/dev/null || echo "0")
COOLDOWN_VAL=$(sed -n '2p' "$STATE_FILE" 2>/dev/null || echo "0")

# Check cooldown
if [[ "$COOLDOWN_VAL" =~ ^COOLDOWN:([0-9]+)$ ]]; then
    COOLDOWN_THRESHOLD="${BASH_REMATCH[1]}"
    if [ "$CURRENT_SIZE" -lt "$COOLDOWN_THRESHOLD" ]; then
        exit 0  # Still within cooldown window
    fi
fi

# Check sampling gate: has JSONL grown enough since last check?
GROWTH=$(( CURRENT_SIZE - LAST_SIZE ))
if [ "$GROWTH" -lt "$MIDCHECK_SAMPLING_BYTES" ]; then
    exit 0  # Not enough new data to warrant a check
fi

# ---- Update state: record current size as new baseline ----
# Update before compute to avoid re-running on transient error.
printf '%s\n%s\n' "$CURRENT_SIZE" "0" > "$STATE_FILE" 2>/dev/null || true

# ---- Compute actual cost ----
RESULT=$(python3 "$SCRIPT_DIR/sum-session-tokens.py" "$JSONL_PATH" "$BASELINE" 2>/dev/null) || exit 0

ACTUAL=$(python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    print(d.get('actual_cost', 0))
except Exception:
    sys.exit(1)
" "$RESULT" 2>/dev/null) || exit 0

# ---- Compare to threshold ----
WARN=$(python3 -c "
import sys
actual = float(sys.argv[1])
pess   = float(sys.argv[2])
threshold = float(sys.argv[3])
if pess <= 0:
    sys.exit(1)
pct = actual / pess
if pct >= threshold:
    print(f'{pct:.0%}:{actual:.4f}:{pess:.4f}')
else:
    sys.exit(1)
" "$ACTUAL" "$PESSIMISTIC" "$MIDCHECK_WARN_THRESHOLD" 2>/dev/null) || exit 0

# ---- Emit warning ----
PCT=$(echo "$WARN" | cut -d: -f1)
ACTUAL_FMT=$(echo "$WARN" | cut -d: -f2)
PESS_FMT=$(echo "$WARN" | cut -d: -f3)

MSG="COST WARNING: Session spend is \$$ACTUAL_FMT, which is $PCT of the pessimistic estimate (\$$PESS_FMT). Consider wrapping up or re-estimating."

python3 -c "
import json, sys
msg = sys.argv[1]
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'additionalContext': msg
    }
}))
" "$MSG"

# ---- Set cooldown to suppress further warnings for ~200KB ----
COOLDOWN_TARGET=$(( CURRENT_SIZE + MIDCHECK_COOLDOWN_BYTES ))
printf '%s\n%s\n' "$CURRENT_SIZE" "COOLDOWN:$COOLDOWN_TARGET" > "$STATE_FILE" 2>/dev/null || true

exit 0
