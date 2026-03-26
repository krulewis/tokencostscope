#!/usr/bin/env bash
# tokencast-track.sh — PostToolUse hook for Agent tool
#
# Fires after the Agent tool returns. Checks if the output looks like
# a plan (keyword matching). If so, injects context nudging Claude
# to run the tokencast skill for a cost estimate.
#
# This is a belt-and-suspenders supplement to the skill's auto-triggering.
# The primary mechanism is Claude's own judgment via the skill description.

set -euo pipefail

# Read hook input from stdin
INPUT=$(cat /dev/stdin 2>/dev/null || echo "{}")

# Extract tool output (first 3000 chars to avoid processing huge outputs)
TOOL_OUTPUT=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    output = str(data.get('tool_output', ''))[:3000]
    print(output)
except:
    print('')
" 2>/dev/null || echo "")

# Check if the agent output looks like a plan
if echo "$TOOL_OUTPUT" | grep -qiE "(implementation plan|final plan|plan complete|architecture decision|files to (change|modify|create)|step.*(1|2|3|4|5)|rollback|test strategy)"; then

    # Check if an estimate already exists for this session
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    SKILL_DIR="$(dirname "$SCRIPT_DIR")"
    ESTIMATE_FILE="$SKILL_DIR/calibration/active-estimate.json"

    if [ -f "$ESTIMATE_FILE" ]; then
        # An estimate already exists — don't nudge again
        exit 0
    fi

    # Output the nudge as hook context
    cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "TOKENCAST: A planning agent just returned what appears to be a plan. If tokencast has not yet estimated this plan, invoke the tokencast skill now to produce a cost estimate before proceeding to implementation."
  }
}
EOF
fi

exit 0
