#!/usr/bin/env bash
# estimate-gate.sh — PreToolUse hook (Agent matcher)
#
# Hard-blocks dispatch of implementation-phase agents (implementer, qa, debugger)
# when calibration/active-estimate.json is absent or older than 24 hours.
#
# Exit 2 = hard block (tool use prevented)
# Exit 0 = allow
#
# IMPORTANT: Do NOT use set -e — find returns non-zero on missing file,
# which would crash the hook rather than trigger the block path.

# Emergency bypass
if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then
  exit 0
fi

INPUT=$(cat)

# Extract subagent_type from tool_input
AGENT_TYPE=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
tool_input = data.get('tool_input', {})
print(tool_input.get('subagent_type', ''))
" 2>/dev/null || true)
AGENT_TYPE="${AGENT_TYPE:-}"

# Only gate implementation-phase agents; planning agents pass freely
case "$AGENT_TYPE" in
  implementer|qa|debugger)
    : # fall through to checks below
    ;;
  *)
    exit 0
    ;;
esac

# Check XS/S size marker — bypass gate for small tasks
# Accepts TOKENCAST_SIZE_MARKER env override for test isolation
SIZE_MARKER="${TOKENCAST_SIZE_MARKER:-${TMPDIR:-/tmp}/tokencast-size-${PPID}}"
if [ -f "$SIZE_MARKER" ]; then
  SIZE_CONTENT=$(cat "$SIZE_MARKER" 2>/dev/null || true)
  case "$SIZE_CONTENT" in
    XS|S)
      exit 0
      ;;
  esac
fi

# Locate calibration directory
# Accepts CALIBRATION_DIR env override for test isolation.
# Default: ~/.tokencast/calibration (where the MCP tool writes active-estimate.json).
# Fallback: repo-local calibration/ (legacy shell-script path).
if [ -n "${CALIBRATION_DIR:-}" ]; then
  CALIB_DIR="$CALIBRATION_DIR"
else
  GLOBAL_CALIB="${HOME}/.tokencast/calibration"
  SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
  PROJECT_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
  LOCAL_CALIB="$PROJECT_ROOT/calibration"
  # Prefer global path (MCP tool); fall back to repo-local (legacy)
  if [ -f "${GLOBAL_CALIB}/active-estimate.json" ]; then
    CALIB_DIR="$GLOBAL_CALIB"
  else
    CALIB_DIR="$LOCAL_CALIB"
  fi
fi

ESTIMATE_FILE="$CALIB_DIR/active-estimate.json"

# Check existence
if [ ! -f "$ESTIMATE_FILE" ]; then
  cat >&2 <<MSG
BLOCKED: No cost estimate recorded.
Run the estimate_cost MCP tool on the final plan before dispatching implementation agents (implementer, qa, debugger).
Missing: $ESTIMATE_FILE
MSG
  exit 2
fi

# Check freshness (24 hours = 1440 minutes)
FRESH=$(find "$ESTIMATE_FILE" -mmin -1440 2>/dev/null || true)
if [ -z "$FRESH" ]; then
  cat >&2 <<MSG
BLOCKED: Cost estimate is stale (older than 24 hours).
Run the estimate_cost MCP tool again for the current plan.
Stale file: $ESTIMATE_FILE
MSG
  exit 2
fi

exit 0
