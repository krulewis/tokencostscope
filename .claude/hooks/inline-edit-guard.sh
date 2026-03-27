#!/usr/bin/env bash
# inline-edit-guard.sh — PostToolUse hook (Edit|Write matcher)
#
# Tracks unique code files edited directly by the orchestrator since the last
# user message. Warns at 3+ files — that scope should be delegated to an agent.
#
# Counter resets on each UserPromptSubmit (via pipeline-gate.sh deleting unique_files.txt).
#
# IMPORTANT: Do NOT use set -e or set -euo pipefail here.
# grep returns exit code 1 on no-match, which would crash the hook.
#
# Exit 0 always (PostToolUse is advisory only).

# Emergency bypass
if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then
  exit 0
fi

INPUT=$(cat)

# Suppress guard when hook is firing inside a dispatched sub-agent.
# The agent_type field is present in the hook envelope when inside a sub-agent.
AGENT_TYPE_CTX=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('agent_type', ''))
" 2>/dev/null || true)
AGENT_TYPE_CTX="${AGENT_TYPE_CTX:-}"

if [ -n "$AGENT_TYPE_CTX" ]; then
  exit 0
fi

# Extract file path from tool_input
FILE_PATH=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
tool_input = data.get('tool_input', {})
print(tool_input.get('file_path', ''))
" 2>/dev/null || true)
FILE_PATH="${FILE_PATH:-}"

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Path filter: only count edits to code paths.
# Non-code paths (docs/, references/, calibration/, CLAUDE.md, etc.) are not counted.
# The markers include trailing slash to prevent false matches on filenames.
IS_CODE_PATH=$(python3 -c "
import sys
path = sys.argv[1]
code_markers = ['/src/', '/tests/', '/scripts/']
print('1' if any(m in path for m in code_markers) else '0')
" "$FILE_PATH" 2>/dev/null || true)

if [ "${IS_CODE_PATH:-0}" != "1" ]; then
  exit 0
fi

# Session tracking — unique files per PPID (Claude process, stable within session)
THRESHOLD=3
SESSION_DIR="${TMPDIR:-/tmp}/tokencast-unique-files-${PPID}"
mkdir -p "$SESSION_DIR" 2>/dev/null || true
UNIQUE_FILES="$SESSION_DIR/unique_files.txt"
touch "$UNIQUE_FILES" 2>/dev/null || true

# Add to unique set (no duplicates)
if ! grep -qxF "$FILE_PATH" "$UNIQUE_FILES" 2>/dev/null; then
  echo "$FILE_PATH" >> "$UNIQUE_FILES"
fi

COUNT=$(wc -l < "$UNIQUE_FILES" | tr -d ' ')

if [ "$COUNT" -ge "$THRESHOLD" ]; then
  FILES=$(awk -F'/' '{print $NF}' "$UNIQUE_FILES" | paste -sd ', ')
  printf '\n'
  printf 'DELEGATION GUARD: You have directly edited %s unique code files this task (%s).\n' "$COUNT" "$FILES"
  printf 'Work touching 3+ files is S/M scope and must be dispatched to an implementer agent.\n'
  printf 'XS exception: single file, <5 tool calls total.\n'
  printf 'Dispatch an implementer or debugger agent instead of editing inline.\n'
fi

exit 0
