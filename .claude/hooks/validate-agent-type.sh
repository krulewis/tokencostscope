#!/usr/bin/env bash
# validate-agent-type.sh — PreToolUse hook (Agent matcher)
#
# Enforces that only custom agents from .claude/agents/ are dispatched.
# Blocks agent types not in the whitelist with a descriptive error.
#
# Exit 2 = hard block
# Exit 0 = allow
#
# IMPORTANT: Do NOT use set -e — python3 failures should produce AGENT_TYPE=""
# (not crash the hook), and the empty-string case exits 0 (fail-open).

# Emergency bypass
if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then
  exit 0
fi

INPUT=$(cat)

# Extract tool name — only validate Agent tool calls
TOOL_NAME=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('tool_name', ''))
" 2>/dev/null || true)
TOOL_NAME="${TOOL_NAME:-}"

if [ "$TOOL_NAME" != "Agent" ]; then
  exit 0
fi

# Extract subagent_type
AGENT_TYPE=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
tool_input = data.get('tool_input', {})
print(tool_input.get('subagent_type', ''))
" 2>/dev/null || true)
AGENT_TYPE="${AGENT_TYPE:-}"

# If extraction failed (empty), fail-open
if [ -z "$AGENT_TYPE" ]; then
  exit 0
fi

# Whitelist: custom agents from .claude/agents/ (project or global)
ALLOWED=(
  "pm"
  "researcher"
  "sr-pm"
  "architect"
  "engineer"
  "implementer"
  "qa"
  "debugger"
  "staff-reviewer"
  "frontend-designer"
  "docs-updater"
  "code-reviewer"
  "explorer"
  "playwright-qa"
)

for allowed in "${ALLOWED[@]}"; do
  if [ "$AGENT_TYPE" = "$allowed" ]; then
    exit 0
  fi
done

# Block with feedback — output to stderr (becomes context for Claude)
cat >&2 <<EOF
Blocked: subagent_type="${AGENT_TYPE}" is not a custom agent.
Use one of the project's custom agents from .claude/agents/:
  ${ALLOWED[*]}
Each has a model assignment in its frontmatter (opus/sonnet/haiku).
EOF
exit 2
