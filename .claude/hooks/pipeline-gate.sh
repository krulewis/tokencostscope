#!/usr/bin/env bash
# pipeline-gate.sh — UserPromptSubmit hook
#
# Fires on every user prompt. Two responsibilities:
#   1. Reset the inline-edit-guard unique-file counter (fresh start each message)
#   2. Inject the pipeline classification reminder for prompts longer than 20 chars
#
# Exit 0 always (UserPromptSubmit is advisory only).

# Emergency bypass — respect SKIP_GATE even for advisory hooks
if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then
  exit 0
fi

INPUT=$(cat)

# Reset inline-edit-guard counter at the start of each user message
SESSION_DIR="${TMPDIR:-/tmp}/tokencast-unique-files-${PPID}"
rm -f "$SESSION_DIR/unique_files.txt" 2>/dev/null || true

# Extract prompt text using python3 (no jq dependency)
PROMPT=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('prompt', ''))
" 2>/dev/null || true)
PROMPT="${PROMPT:-}"
PROMPT_LEN=${#PROMPT}

# Skip for very short prompts (confirmations, "yes", "continue", etc.)
if [ "$PROMPT_LEN" -lt 20 ]; then
  exit 0
fi

cat <<'EOF'
<pipeline-gate>
PIPELINE GATE — Before starting work, classify this task:

| Size | Description                                    | Pipeline Required?             |
|------|------------------------------------------------|--------------------------------|
| XS   | Single file, < 5 lines, no tests affected      | No — execute inline            |
| S    | 1-2 files, clear scope                         | Optional                       |
| M    | Multi-file, new feature, involves tests        | YES — full pipeline            |
| L    | New systems, architectural decisions           | YES — full pipeline            |

If M or L:
1. State the classification and why
2. Run the planning pipeline: pm -> researcher -> architect -> (frontend-designer if UI) -> engineer -> staff-reviewer -> engineer (final)
3. Run /tokencast on the final plan BEFORE dispatching implementer/qa/debugger
4. Do NOT skip steps or combine agents — each must be a fresh-context dispatch

If XS or S:
1. State the classification briefly
2. Proceed directly (<5 tool calls may execute inline per CLAUDE.md)

ENFORCEMENT: estimate-gate.sh will HARD-BLOCK implementer/qa/debugger dispatch if no current active-estimate.json exists.
ALWAYS state the classification before doing any work.
</pipeline-gate>
EOF

exit 0
