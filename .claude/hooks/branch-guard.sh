#!/usr/bin/env bash
# branch-guard.sh — PreToolUse hook (Bash matcher)
#
# Blocks two operations:
#   1. git commit on the main branch (unconditionally)
#   2. git push without a review-complete marker file
#
# Exit 2 = hard block
# Exit 0 = allow
#
# IMPORTANT: Do NOT use set -e — grep returns 1 on no-match,
# which would crash the hook rather than fall through to exit 0.
#
# Marker file uses $PPID (Claude process PID) — stable within a session,
# matches monarch pre-push-gate.sh pattern.

# Emergency bypass
if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then
  exit 0
fi

INPUT=$(cat)

# Compute marker file path at the top so it's always available for error messages
MARKER_FILE="${TMPDIR:-/tmp}/tokencast-push-reviewed-${PPID}"

# Extract the bash command
COMMAND=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
tool_input = data.get('tool_input', data)
print(tool_input.get('command', ''))
" 2>/dev/null || true)
COMMAND="${COMMAND:-}"

if [ -z "$COMMAND" ]; then
  exit 0
fi

# Strip $(...) subexpressions and -m "..." / -m '...' args.
# This prevents commit messages containing "git push" or "git commit" from
# triggering the gate (the message text is not a command invocation).
STRIPPED=$(python3 - "$COMMAND" <<'PYEOF'
import sys, re

cmd = sys.argv[1]

# Remove $(...) at any nesting depth (character-level depth counter)
result = []
depth = 0
i = 0
while i < len(cmd):
    if cmd[i:i+2] == '$(':
        depth += 1
        i += 2
        continue
    elif cmd[i] == ')' and depth > 0:
        depth -= 1
        i += 1
        continue
    elif depth > 0:
        i += 1
        continue
    result.append(cmd[i])
    i += 1

stripped = ''.join(result)

# Strip -m "..." (double-quoted commit message)
stripped = re.sub(r'-m\s+"[^"]*"', '', stripped)
# Strip -m '...' (single-quoted commit message)
stripped = re.sub(r"-m\s+'[^']*'", '', stripped)

print(stripped)
PYEOF
) || STRIPPED="$COMMAND"

# Detect git commit and git push in the stripped command
IS_COMMIT=0
IS_PUSH=0
echo "$STRIPPED" | grep -qE '(^|[;&|])\s*git\s+commit' && IS_COMMIT=1 || true
echo "$STRIPPED" | grep -qE '(^|[;&|])\s*git\s+push' && IS_PUSH=1 || true

# If neither commit nor push, exit 0 (not relevant)
if [ "$IS_COMMIT" -eq 0 ] && [ "$IS_PUSH" -eq 0 ]; then
  exit 0
fi

# Get current branch — fail-open on detached HEAD or bare repo
CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || true)
if [ -z "$CURRENT_BRANCH" ]; then
  exit 0
fi

# Block commits directly to main
if [ "$IS_COMMIT" -eq 1 ] && [ "$CURRENT_BRANCH" = "main" ]; then
  cat >&2 <<'MSG'
BLOCKED: Direct commits to main are not allowed.
Create a feature branch first: git checkout -b <feature-name>
Direct commits to main are prohibited; only merge commits from approved PRs are allowed.
MSG
  exit 2
fi

# Block pushes without review marker (any branch)
if [ "$IS_PUSH" -eq 1 ]; then
  if [ -f "$MARKER_FILE" ]; then
    # Consume the marker — one push allowed per review confirmation
    rm -f "$MARKER_FILE"
    exit 0
  fi

  cat >&2 <<EOF
BLOCKED: Push requires completed PR review loop.
Confirm before pushing:
  1. All tests pass
  2. staff-reviewer agent found no remaining comments (clean pass)
  3. docs-updater has been dispatched

Once complete, allow the push by running:
  touch '${MARKER_FILE}'
Then re-run the push command.
EOF
  exit 2
fi

exit 0
