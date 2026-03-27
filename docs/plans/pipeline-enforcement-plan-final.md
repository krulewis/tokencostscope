# Pipeline Enforcement — FINAL Implementation Plan

*Date: 2026-03-26*
*Author: Engineer Agent (Final Pass)*
*Status: Final Plan — incorporates staff review findings*
*Inputs: pipeline-enforcement-plan.md (initial), pipeline-enforcement-architecture.md, monarch reference hooks, staff review patterns*

---

## Staff Review Findings — How Each Was Addressed

The staff review found 2 CRITICAL, 4 HIGH, 6 MEDIUM, and 3 LOW findings. Every finding is addressed below.

### CRITICAL-1: $PPID is unreliable for marker file namespacing

**Finding:** On macOS, `$PPID` in a hook script is the shell's parent PID, which is the Claude Code process — not the session ID. When multiple concurrent hooks fire in the same Claude session, they all share the same `$PPID`. This is not the cross-session collision risk (marker files are `$TMPDIR` which is session-scoped in practice), but it IS a problem for tests: two test processes running concurrently will collide on the same marker file path if both use `$PPID`.

**Resolution:** All marker file paths now use `$$` (current shell PID) instead of `$PPID`. The current shell PID is unique per hook invocation, which is what we want: each hook invocation gets its own namespace slot. For the push-reviewed marker specifically, the marker path is echoed in the block message so the user knows exactly which path to `touch` — the hook re-derives the path on next invocation using the same formula (i.e., the hook's own `$$`, not the external process's `$$`).

**Wait — on re-examination:** The push-reviewed marker must be written by the orchestrator BEFORE the hook fires (the human or orchestrator runs `touch marker-path`). If the hook uses `$$` (hook's PID), the orchestrator cannot predict that PID in advance. The marker file path must be deterministic and predictable. The correct approach is to use a deterministic, session-stable identifier. The monarch implementation uses `$PPID` (the Claude process PID, which IS stable across all hooks within one session). We keep `$PPID` for the push-reviewed marker (it's what monarch uses and it works), but we add a comment documenting why. For test isolation, tests must set a unique `TMPDIR` per test.

**Actual resolution:** Keep `$PPID` for all marker files (matches monarch, is deterministic within a session). Document the test isolation requirement explicitly: each test must set `TMPDIR=$(mktemp -d)` in the subprocess env so tests don't collide. The test strategy section specifies this.

### CRITICAL-2: env-var injection pattern for Python JSON parsing is inconsistent

**Finding:** The initial plan described using `export HOOK_INPUT="$INPUT"` and reading `os.environ['HOOK_INPUT']` inside Python — the "injection-safe" pattern from midcheck.sh. But the monarch scripts (which we are porting) use `echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); ..."` — direct pipe, not env var. The inconsistency means the hooks would not match each other or the reference implementation, and the env-var pattern is fragile when `$INPUT` contains newlines (the JSON value is multi-line after `cat`).

**Resolution:** All hooks use the direct pipe pattern: `echo "$INPUT" | python3 -c "import json,sys; ..."`. This is exactly what monarch uses. The env-var pattern (midcheck.sh) is for a different scenario where the JSON is constructed inline. The pipe pattern handles multi-line JSON correctly because `json.load(sys.stdin)` reads the full stream.

### HIGH-1: settings.json uses relative path `./.claude/hooks/` — unreliable when cwd is not repo root

**Finding:** Relative paths in hook commands depend on Claude Code's working directory being the repo root. When hooks fire inside a sub-agent or from a different working directory, relative paths fail silently (command not found, exit 0 from error handler, gate bypassed).

**Resolution:** All new hook commands in settings.json use absolute paths with the space-containing path properly quoted: `bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh'`. This matches the existing pattern for all other hooks in settings.json (they all use absolute paths with `bash '...'` wrapper). The initial plan's use of `./.claude/hooks/` is replaced throughout.

### HIGH-2: validate-agent-type.sh uses `jq` which may not be installed

**Finding:** The monarch validate-agent-type.sh uses `jq -r '.tool_input.subagent_type // empty'`. jq is not part of macOS's built-in toolset. If jq is absent, the hook crashes and exits with a non-zero status from the `jq` call; if `set -e` is active, the hook exits 1, which is NOT a hard block but may suppress output. The fallback `|| true` is not present in the monarch version.

**Resolution:** Replace all `jq` calls with `python3 -c "import json,sys; ..."` inline Python, which is available on all macOS systems and matches the pattern used in the other hooks. This removes the jq dependency entirely.

### HIGH-3: branch-guard.sh does not strip `-m '...'` single-quoted commit messages with embedded git push

**Finding:** The monarch pre-push-gate.sh Python stripping regex uses `re.sub(r"-m\s+\"[^\"]*\"", '', stripped)` for double-quoted commit messages only. A commit message like `git commit -m 'ensure we never git push to main'` would not be stripped, causing `git push` inside the commit message to trigger the push gate.

**Resolution:** The Python stripping code must handle both single and double-quoted `-m` args. The final script includes both patterns exactly as described.

### HIGH-4: inline-edit-guard.sh path filter uses substring matching that could match doc paths containing 'src'

**Finding:** The path filter using `grep -q '/src/'` would match `/docs/enterprise-strategy-src-review.md` or any path that happens to contain `/src/`. The filter should check path components, not substrings.

**Resolution:** Path filtering uses a Python check that tests each prefix independently against the full path, and only counts the file if ANY of the code paths (`/src/`, `/tests/`, `/scripts/`) appear as directory component boundaries. Specifically: `any(marker in file_path for marker in ['/src/', '/tests/', '/scripts/'])` — this still uses substring matching but the markers include the trailing slash, so `/docs/src-archive/` would match but `/docs/enterprise-strategy-src-review.md` would not. For full safety, we additionally require the path component to begin with one of the known project roots or contain the component at a directory boundary. The safest approach for the actual project layout: use `os.path` normalization with a check against the project's known source directories. The script will use the Python substring approach with `/src/`, `/tests/`, `/scripts/` — which is safe for the tokencast project's actual directory layout (no paths contain these substrings except the actual source directories).

### MEDIUM-1: estimate-gate.sh CALIBRATION_DIR path derivation hardcodes directory structure

**Finding:** `SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd); PROJECT_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)` assumes the script is always at `.claude/hooks/estimate-gate.sh` relative to the project root (two levels up). This is correct for the current layout but is not explicit.

**Resolution:** Add a comment: `# Script is at .claude/hooks/ — two levels up is project root`. Also add a fallback: if `$CALIBRATION_DIR` env var is set, use it instead (enables test isolation). This is the `CALIBRATION_DIR` env override pattern.

### MEDIUM-2: pre-compact-reminder.sh and pipeline-gate.sh have no SKIP_GATE check but plan says "advisory"

**Finding:** The architecture says advisory hooks should still respect SKIP_GATE. The plan explicitly says `pre-compact-reminder.sh` does not need a SKIP_GATE check ("advisory reminder, always output"), but pipeline-gate.sh also omits the check with a different rationale. This is inconsistent — if someone sets SKIP_GATE=1 to get through a broken session, they don't want the pipeline gate firing noise on every prompt.

**Resolution:** Both advisory hooks check SKIP_GATE=1 at the top and exit 0 if set. The T20 test already covers "all hooks pass SKIP_GATE=1" — this must include pre-compact-reminder.sh and pipeline-gate.sh.

### MEDIUM-3: branch-guard.sh push marker message contains unexpanded `${MARKER_FILE}` if variable is empty

**Finding:** The stderr message template contains `touch ${MARKER_FILE}` but the MARKER_FILE variable is set after the IS_COMMIT check. If for some reason the variable is empty (defensive), the instruction would say `touch ` with no path.

**Resolution:** Compute MARKER_FILE at the top of the script (before IS_COMMIT/IS_PUSH checks), so it is always available for the error message.

### MEDIUM-4: validate-agent-type.sh uses `set -e` (from monarch) but plan says "no set -e"

**Finding:** The monarch validate-agent-type.sh has `set -e` at line 5. The plan says "No set -e at the top (monarch source doesn't use it)". This is incorrect — monarch DOES use `set -e`. With `set -e`, if the python3 call fails, the hook exits 1 (not 2), which means unknown agents pass silently.

**Resolution:** Do NOT use `set -e`. Use `|| true` / `|| AGENT_TYPE=""` on each fallible command. This matches the plan's intent (which correctly identified the problem but incorrectly stated monarch doesn't use it).

### MEDIUM-5: Size marker file uses `$PPID` but test T6 uses `$$`

**Finding:** Test T6 writes the size marker with `echo "XS" > "${TMPDIR:-/tmp}/tokencast-size-$$"` but the hook reads it with `$PPID`. When running the test via subprocess, the hook's `$PPID` is the test process's PID, which equals the `$$` used in the test. But if the test uses `subprocess.run`, the subprocess's `$PPID` is the pytest process PID, not the shell's PID. This means T6 will fail unless the test explicitly sets `TMPDIR` and creates the marker at the path the hook will look for.

**Resolution:** Test T6 in the automated test file must use a controlled `TMPDIR` and pre-create the marker file at `$TMPDIR/tokencast-size-<ppid>` where `<ppid>` is the PID of the bash process being invoked (not trivially predictable). The simpler fix: the hook accepts a `TOKENCAST_SIZE_MARKER` env var override. If set, use that path; otherwise use the default `$TMPDIR/tokencast-size-$PPID`. Tests set `TOKENCAST_SIZE_MARKER` to a controlled path.

### MEDIUM-6: pipeline-gate.sh uses `jq` for prompt extraction (monarch uses jq)

**Finding:** The monarch pipeline-gate.sh uses `jq -r '.prompt // empty'`. Same jq dependency problem as HIGH-2.

**Resolution:** Replace with python3 inline, consistent with all other hooks.

### LOW-1: Missing `chmod +x` in the implementation plan — implementer may forget

**Finding:** The plan says "Make executable: chmod +x" in the Details section but does not specify where this happens. If an implementer creates the files with Write tool, they won't be executable by default, and the hooks will fail silently (command not found or permission denied, hook exits non-zero, fail-open).

**Resolution:** The implementation plan explicitly calls out that after each file is written, the implementer MUST run `chmod +x` on it. The final plan groups all `chmod +x` calls into a dedicated Round 1.5 step that must complete before settings.json registration.

### LOW-2: No `.gitignore` entry for `.claude/hooks/` ephemeral files

**Finding:** The marker files `$TMPDIR/tokencast-push-reviewed-$PPID` and `$TMPDIR/tokencast-size-$PPID` are in `$TMPDIR`, which is not in the repo. But the `$TMPDIR/tokencast-unique-files-$PPID/` session directory is also in `$TMPDIR`. No .gitignore issue, but the `.claude/hooks/` directory itself is not gitignored — the scripts should be committed (they are config). This is correct: hooks are committed, ephemeral markers are in `$TMPDIR` (not committed). No action needed, but document it.

**Resolution:** Add a note in CLAUDE.md that `.claude/hooks/` scripts are checked into git (they are project config, not runtime data). Document that only `calibration/` is gitignored.

### LOW-3: The `sr-pm` agent in the allowed list is not defined in agents/

**Finding:** The plan adds `sr-pm` to the validate-agent-type.sh whitelist citing it appears in CLAUDE.md pipeline steps. But `sr-pm` is not defined as a custom agent file in `.claude/agents/`. Adding it to the whitelist but not having the agent definition means dispatching `sr-pm` will fail with "agent not found" — a confusing error after the whitelist passes.

**Resolution:** Remove `sr-pm` from the whitelist. It does not have an agent definition. If someone dispatches it, the "agent not found" error is more helpful than the whitelist passing and then failing later. This matches the principle: the whitelist covers agents that EXIST in `.claude/agents/`.

---

## Overview

Implement six hook scripts in `.claude/hooks/` plus settings.json registration. Three hooks are hard-block PreToolUse gates; three are advisory (PostToolUse, PreCompact, UserPromptSubmit). All hooks check `TOKENCAST_SKIP_GATE=1` first. All hooks are fail-safe: no `set -e`, every fallible command has `|| true` or `|| exit 0`. All new hooks use absolute paths in settings.json to match the existing pattern.

This plan is implementer-ready: each file section contains the full script content, ready to write verbatim.

---

## Changes

### Group A: New Hook Scripts (all independent, can be written in parallel)

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh
Lines: new file
Parallelism: independent
Description: PreToolUse hook. Hard-blocks implementer/qa/debugger agent dispatch when active-estimate.json is absent or stale (>24h). Accepts CALIBRATION_DIR and TOKENCAST_SIZE_MARKER env overrides for test isolation.
```

**Full script content:**

```bash
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
# Accepts CALIBRATION_DIR env override for test isolation
if [ -n "${CALIBRATION_DIR:-}" ]; then
  CALIB_DIR="$CALIBRATION_DIR"
else
  # Script is at .claude/hooks/ — two levels up is project root
  SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
  PROJECT_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
  CALIB_DIR="$PROJECT_ROOT/calibration"
fi

ESTIMATE_FILE="$CALIB_DIR/active-estimate.json"

# Check existence
if [ ! -f "$ESTIMATE_FILE" ]; then
  cat >&2 <<'MSG'
BLOCKED: No cost estimate recorded.
Run /tokencast on the final plan before dispatching implementation agents (implementer, qa, debugger).
Missing: calibration/active-estimate.json
MSG
  exit 2
fi

# Check freshness (24 hours = 1440 minutes)
FRESH=$(find "$ESTIMATE_FILE" -mmin -1440 2>/dev/null || true)
if [ -z "$FRESH" ]; then
  cat >&2 <<'MSG'
BLOCKED: Cost estimate is stale (older than 24 hours).
Run /tokencast again for the current plan.
Stale file: calibration/active-estimate.json
MSG
  exit 2
fi

exit 0
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/validate-agent-type.sh
Lines: new file
Parallelism: independent
Description: PreToolUse hook (Agent matcher). Whitelists allowed agent types. Hard-blocks unknown agent types. No jq dependency — uses python3 inline. No set -e.
```

**Full script content:**

```bash
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

# Whitelist: custom agents defined in .claude/agents/
# Note: sr-pm is intentionally excluded — no agent definition file exists
ALLOWED=(
  "pm"
  "researcher"
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

# Block with feedback — output to stdout (becomes context for Claude)
cat >&2 <<EOF
Blocked: subagent_type="${AGENT_TYPE}" is not a custom agent.
Use one of the project's custom agents from .claude/agents/:
  ${ALLOWED[*]}
Each has a model assignment in its frontmatter (opus/sonnet/haiku).
EOF
exit 2
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/branch-guard.sh
Lines: new file
Parallelism: independent
Description: PreToolUse hook (Bash matcher). Hard-blocks git commit on main. Hard-blocks git push without push-reviewed marker. Extends monarch pre-push-gate.sh with commit detection and SKIP_GATE. Uses $PPID for marker (deterministic within a session, matches monarch pattern).
```

**Full script content:**

```bash
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
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/inline-edit-guard.sh
Lines: new file
Parallelism: independent
Description: PostToolUse hook (Edit|Write matcher). Warns when the orchestrator (not a sub-agent) directly edits 3+ unique code files. Exits 0 always (advisory). Adds: agent_type suppression, code-path filtering, SKIP_GATE check, tokencast-prefixed session dir.
```

**Full script content:**

```bash
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
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pre-compact-reminder.sh
Lines: new file
Parallelism: independent
Description: PreCompact hook. Outputs pipeline state reminder before context compaction. Advisory — exits 0 always. Checks SKIP_GATE per staff review finding MEDIUM-2.
```

**Full script content:**

```bash
#!/usr/bin/env bash
# pre-compact-reminder.sh — PreCompact hook
#
# Fires before context compaction to remind the orchestrator about pipeline
# enforcement rules, which are easy to miss after losing conversation context.
#
# Exit 0 always (PreCompact cannot block; this is advisory).

# Emergency bypass — respect SKIP_GATE even for advisory hooks
if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then
  exit 0
fi

cat <<'MSG'
CONTEXT COMPACTED — Pipeline enforcement reminder:
- calibration/active-estimate.json MUST exist before dispatching implementer/qa/debugger agents
- If the estimate file is missing or stale (>24h), run /tokencast on the final plan
- All multi-file work (3+ files) MUST be dispatched to implementer/debugger agents
- XS exception (inline ok): single file, <5 tool calls total
- Do NOT edit src/, tests/, or scripts/ files directly if scope is S/M/L
- If working on an M/L story, verify the full planning pipeline is complete (PP-1 through PP-7)
- Direct commits to main are blocked by branch-guard.sh — always use a feature branch
- Resume by re-reading the compaction summary, then dispatch agents as needed
MSG

exit 0
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pipeline-gate.sh
Lines: new file
Parallelism: independent
Description: UserPromptSubmit hook. Injects pipeline classification reminder on prompts >20 chars. Resets inline-edit-guard counter. No jq dependency — uses python3. Checks SKIP_GATE per staff review finding MEDIUM-2.
```

**Full script content:**

```bash
#!/bin/bash
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
```

---

### Group A.5: Make hooks executable (depends on Group A files existing)

After all six scripts are written, run:

```bash
chmod +x '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh'
chmod +x '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/validate-agent-type.sh'
chmod +x '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/branch-guard.sh'
chmod +x '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/inline-edit-guard.sh'
chmod +x '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pre-compact-reminder.sh'
chmod +x '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pipeline-gate.sh'
```

**This step is required.** Hooks that are not executable will fail with "permission denied" — the error is non-zero, the hook exits silently (fail-open), and the gate is bypassed with no warning.

---

### Group B: settings.json update

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/settings.json
Lines: 1-53 (full replacement)
Parallelism: depends-on: Group A.5 (hooks must be executable before registering)
Description: Add six new hook registrations. All new entries use absolute paths with bash wrapper to match the existing pattern. No relative paths.
```

**Full file content (ready to Write verbatim):**

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-learn.sh'"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-midcheck.sh'"
          }
        ]
      },
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-agent-hook.sh'"
          }
        ]
      },
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh'"
          }
        ]
      },
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/validate-agent-type.sh'"
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/branch-guard.sh'"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-track.sh'"
          }
        ]
      },
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-agent-hook.sh'"
          }
        ]
      },
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/inline-edit-guard.sh'"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pre-compact-reminder.sh'"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pipeline-gate.sh'"
          }
        ]
      }
    ]
  }
}
```

**Key differences from initial plan:**
- All new hook commands use `bash '/absolute/path/...'` pattern (not `./.claude/hooks/...`)
- Absolute path with `bash '...'` wrapper handles the space in "Macintosh HD2"

---

### Group C: CLAUDE.md updates (independent of Groups A and B)

```
File: /Users/kellyl./.claude/CLAUDE.md
Lines: targeted insertions at specific locations
Parallelism: independent
Description: Add enforcement references at four locations.
```

**CHANGE 1** — After the "2. **Confirm** approach..." step and before "3. **Write tests first**", insert:

```
2a. **Enforcement gate.** For M/L changes, `calibration/active-estimate.json` MUST exist before dispatching implementation-phase agents (`implementer`, `qa`, `debugger`). The `estimate-gate.sh` hook hard-blocks these dispatches when no estimate exists or is stale (>24h). If blocked, run `/tokencast` on the final plan before proceeding.
```

**CHANGE 2** — Replace step 3 entirely with:

```
3. **Write tests first** — dispatch to `qa` agent. Tests must fail before implementation exists. Cover happy path, edge cases, and error cases. The orchestrator MUST NOT dispatch `implementer` agents until `qa` has been dispatched and tests committed to the feature branch.
```

**CHANGE 3** — Replace step 8 entirely with:

```
8. **Commit to feature branch** — push and create PR against main via `gh pr create`. The `branch-guard.sh` hook hard-blocks `git commit` and `git push` on the `main` branch. Always create a feature branch before committing: `git checkout -b <feature-name>`. To allow a push after the PR review loop completes, run: `touch "${TMPDIR:-/tmp}/tokencast-push-reviewed-$$"` (where `$$` is the Claude process PID shown in the block message).
```

**CHANGE 4** — In the "Agent Delegation — MANDATORY" section, after the `**Exception:**` paragraph, add:

```
**Hook enforcement:** The `inline-edit-guard.sh` hook warns when the orchestrator edits 3+ unique code files in `src/`, `tests/`, or `scripts/` without dispatching an agent. The `validate-agent-type.sh` hook hard-blocks dispatch of unrecognized agent types. The `estimate-gate.sh` hook hard-blocks implementation agents when `calibration/active-estimate.json` is absent or stale.

**Emergency bypass:** Set `TOKENCAST_SKIP_GATE=1` in the environment to disable all enforcement hooks. Use only for genuine emergencies. Its use is visible in the session transcript.
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/CLAUDE.md
Lines: targeted additions at three locations
Parallelism: independent
Description: Document hook enforcement system, architecture conventions, and gotchas.
```

**CHANGE 1** — Add new section after the "## Key Files" table (after the closing row of the table, before "## Test Commands"):

```markdown
## Hook Enforcement

Six enforcement hooks in `.claude/hooks/` hard-block the two highest-frequency pipeline violations and inject advisory guardrails for others. All hooks are committed to git (they are project config, not runtime data).

| Hook | Event | Type | Purpose |
|------|-------|------|---------|
| `estimate-gate.sh` | PreToolUse (Agent) | HARD BLOCK | Blocks implementer/qa/debugger dispatch without fresh active-estimate.json |
| `validate-agent-type.sh` | PreToolUse (Agent) | HARD BLOCK | Blocks unknown agent types not in .claude/agents/ |
| `branch-guard.sh` | PreToolUse (Bash) | HARD BLOCK | Blocks git commit on main; blocks git push without review marker |
| `inline-edit-guard.sh` | PostToolUse (Edit/Write) | Advisory | Warns at 3+ unique code files edited directly by orchestrator |
| `pre-compact-reminder.sh` | PreCompact | Advisory | Injects pipeline state reminder before compaction |
| `pipeline-gate.sh` | UserPromptSubmit | Advisory | Injects classification reminder; resets edit counter |

**Emergency bypass:** Set `TOKENCAST_SKIP_GATE=1` to bypass all gates. Use only for genuine emergencies.

**Push review gate:** After the PR review loop is complete (staff-reviewer: no remaining comments), allow the push by running:
```bash
touch "${TMPDIR:-/tmp}/tokencast-push-reviewed-${PPID}"
```
(The exact path is shown in the block message when the push is blocked.)
```

**CHANGE 2** — In "## Architecture Conventions", add a bullet:

```
- **Hook placement:** Enforcement hooks live in `.claude/hooks/` (not `scripts/`). Core tokencast functionality remains in `scripts/`. Enforcement hooks use `bash '/absolute/path/...'` in `settings.json` to match the existing hook pattern and handle the space in "Macintosh HD2".
```

**CHANGE 3** — In "## Gotchas", add a bullet:

```
- **Enforcement hooks:** All hooks in `.claude/hooks/` check `TOKENCAST_SKIP_GATE=1` first and exit 0 if set. `inline-edit-guard.sh` suppresses warnings when `agent_type` is present in the hook envelope (sub-agent context). `branch-guard.sh` uses `|| true` around `git branch --show-current` to fail-open in detached HEAD state. `validate-agent-type.sh` has no `set -e` — python3 failures produce `AGENT_TYPE=""` which exits 0 (fail-open). `estimate-gate.sh` accepts `CALIBRATION_DIR` and `TOKENCAST_SIZE_MARKER` env overrides for test isolation.
```

---

### Group D: Automated Test File

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_pipeline_enforcement.py
Lines: new file
Parallelism: independent (can be written in parallel with Group A — interfaces are fully defined)
Description: Python test file. subprocess invocations of each hook with synthetic JSON payloads. Each test uses isolated TMPDIR to prevent marker file collisions.
```

**Key implementation requirements for the test file (implementer must follow these):**

1. Every test that creates subprocess must pass `env={**os.environ, "TMPDIR": tmp_dir}` so marker files go to an isolated directory. Never rely on the default `$TMPDIR` across tests.

2. For `estimate-gate.sh` tests, pass `CALIBRATION_DIR` in env to point to a temp calibration directory. Do not use the real `calibration/` directory.

3. For size marker tests (T6), pass `TOKENCAST_SIZE_MARKER` in env pointing to a pre-created file in the test's temp dir.

4. For `branch-guard.sh` tests, mock `git` by creating a fake git script in a temp `bin/` dir:
   ```python
   fake_git = os.path.join(tmp_dir, 'bin', 'git')
   os.makedirs(os.path.dirname(fake_git))
   with open(fake_git, 'w') as f:
       f.write('#!/bin/sh\nif [ "$1" = "branch" ] && [ "$2" = "--show-current" ]; then echo "main"; exit 0; fi\nexit 0\n')
   os.chmod(fake_git, 0o755)
   env = {**os.environ, "TMPDIR": tmp_dir, "PATH": f"{os.path.dirname(fake_git)}:{os.environ['PATH']}"}
   ```

5. The subprocess invocation pattern:
   ```python
   result = subprocess.run(
       ["bash", hook_path],
       input=json_payload.encode(),
       capture_output=True,
       env=env
   )
   ```

6. Assert `result.returncode` (0 or 2) and `result.stderr` for block messages.

7. Use `/usr/bin/python3` path only in comments; the test file itself runs under pytest which uses `/usr/bin/python3 -m pytest` per CLAUDE.md.

**Test classes and cases:**

```
TestEstimateGate:
  test_missing_estimate_blocks_implementer (T1: exit 2)
  test_fresh_estimate_allows_implementer (T2: exit 0)
  test_planning_agent_passes_without_estimate (T3: exit 0)
  test_skip_gate_bypasses_block (T4: TOKENCAST_SKIP_GATE=1, exit 0)
  test_stale_estimate_blocks_implementer (T5: touch -t, exit 2)
  test_xs_size_marker_bypasses_gate (T6: TOKENCAST_SIZE_MARKER with XS content, exit 0)
  test_s_size_marker_bypasses_gate (T6b: TOKENCAST_SIZE_MARKER with S content, exit 0)
  test_empty_size_marker_does_not_bypass (T6c: marker file with "M" content, exit 2)

TestValidateAgentType:
  test_known_agent_passes (T7: implementer, exit 0)
  test_unknown_agent_blocked (T8: "gpt-researcher", exit 2, stderr contains "not a custom agent")
  test_non_agent_tool_passes (tool_name="Bash", exit 0)
  test_skip_gate_bypasses (TOKENCAST_SKIP_GATE=1, unknown agent, exit 0)
  test_sr_pm_is_blocked (sr-pm is not in whitelist, exit 2)

TestBranchGuard:
  test_commit_on_main_blocked (T9: fake git returns "main", commit command, exit 2)
  test_commit_on_feature_branch_passes (fake git returns "feature-xyz", exit 0)
  test_commit_message_with_git_push_not_triggered (T10: -m "do not git push", exit 0)
  test_push_without_marker_blocked (T11: exit 2, stderr contains "BLOCKED: Push requires")
  test_push_with_marker_passes_and_consumes_marker (T12: pre-create marker, exit 0, marker gone)
  test_non_git_command_passes (echo "hello", exit 0)
  test_skip_gate_bypasses (TOKENCAST_SKIP_GATE=1, commit on main, exit 0)

TestInlineEditGuard:
  test_sub_agent_context_suppressed (T13: agent_type="implementer" in JSON, exit 0, no output)
  test_three_unique_code_files_warns (T14: 3 src/ paths, third triggers warning)
  test_docs_path_not_counted (T15: 4 docs/ paths, no warning)
  test_skip_gate_bypasses (TOKENCAST_SKIP_GATE=1, exit 0, no output)
  test_references_path_not_counted (references/heuristics.md, exit 0, no output)
  test_scripts_path_counted (scripts/update-factors.py, counted as code path)
  test_second_edit_same_file_not_double_counted (same path twice, count stays at 1)

TestPipelineGate:
  test_short_prompt_suppressed (T16: "yes", no output)
  test_long_prompt_injects_reminder (T17: full sentence, output contains "PIPELINE GATE")
  test_resets_inline_edit_counter (T18: pre-populate unique_files.txt, assert deleted after)
  test_skip_gate_suppresses_output (TOKENCAST_SKIP_GATE=1, no output)

TestPreCompactReminder:
  test_outputs_reminder (T19: output contains "CONTEXT COMPACTED" and "active-estimate.json")
  test_skip_gate_suppresses (TOKENCAST_SKIP_GATE=1, no output)

TestSkipGate:
  test_all_hooks_pass_with_skip_gate (T20: each hook with TOKENCAST_SKIP_GATE=1, all exit 0)
```

---

## Dependency Order

```
Round 1 (all parallel — no dependencies):
  A1: .claude/hooks/estimate-gate.sh       (write + chmod)
  A2: .claude/hooks/validate-agent-type.sh  (write + chmod)
  A3: .claude/hooks/branch-guard.sh         (write + chmod)
  A4: .claude/hooks/inline-edit-guard.sh    (write + chmod)
  A5: .claude/hooks/pre-compact-reminder.sh (write + chmod)
  A6: .claude/hooks/pipeline-gate.sh        (write + chmod)
  C1: ~/.claude/CLAUDE.md (targeted insertions)
  C2: /Volumes/Macintosh HD2/Cowork/Projects/costscope/CLAUDE.md (targeted additions)
  Test: tests/test_pipeline_enforcement.py  (new file — interfaces are fully defined)

Round 2 (depends on A1–A6 being executable):
  B1: .claude/settings.json (full replacement)

Phase 2 (future — not in this delivery):
  D1: .claude/hooks/pipeline-state-recorder.sh
  D2: settings.json addition for D1
  D3: estimate-gate.sh extension to read pipeline-state.json
  D4: branch-guard.sh extension to check docs_updated and review_loop.status
  D5: pipeline-gate.sh extension to inject pipeline-state.json
```

---

## Test Strategy

### Happy Path Tests (one per hook)
- estimate-gate: fresh active-estimate.json + implementer agent type → exit 0
- validate-agent-type: known agent type → exit 0
- branch-guard: git push on feature branch with marker → exit 0, marker consumed
- inline-edit-guard: 2 unique code files → no warning, exit 0
- pre-compact-reminder: any input → reminder text output, exit 0
- pipeline-gate: prompt >20 chars → classification table output, exit 0

### Block/Hard-Gate Tests
- estimate-gate: missing file, stale file, both → exit 2 with correct stderr
- validate-agent-type: unknown agent type → exit 2 with "not a custom agent"
- branch-guard: commit on main → exit 2; push without marker → exit 2
- inline-edit-guard: 3rd unique code file → warning output (exit 0 still, PostToolUse)

### Escape Hatch Tests (every hook)
- TOKENCAST_SKIP_GATE=1 on all six hooks → exit 0 regardless of conditions

### Edge Cases
- estimate-gate with XS/S size marker → exit 0 (gate bypassed)
- estimate-gate with empty size marker content → gate applies
- branch-guard: commit message containing "git push" (after -m stripping) → not triggered
- branch-guard: single-quoted commit message with "git push" → not triggered (HIGH-3 fix)
- branch-guard: detached HEAD (git returns empty branch) → exit 0 (fail-open)
- validate-agent-type: non-Agent tool (Bash) → exit 0
- validate-agent-type: sr-pm → exit 2 (not in whitelist, no agent definition)
- inline-edit-guard: same file edited twice → count stays at 1 (dedup)
- inline-edit-guard: docs/ path, references/ path → not counted
- inline-edit-guard: agent_type present in hook input → exit 0 (sub-agent suppression)
- pipeline-gate: very short prompt ("ok") → no output
- pipeline-gate: counter reset verifiable via unique_files.txt existence check

### Test Isolation Requirements
- Each test creates its own `TMPDIR=$(tempfile.mkdtemp())` and passes it via env
- estimate-gate tests pass `CALIBRATION_DIR` env var to isolated temp dir
- XS/S bypass tests pass `TOKENCAST_SIZE_MARKER` env var
- branch-guard tests inject fake `git` via PATH override
- No test relies on real `calibration/` directory state
- After each test, `tearDown` or `addCleanup` removes the temp dir

### Existing Tests — Impact
The six new shell scripts have zero interaction with the existing Python modules. The 441 existing tests (`test_pr_review_loop.py`, `test_parallel_agent_accounting.py`, `test_file_size_awareness.py`, etc.) are not affected. `settings.json` changes do not affect pytest. CLAUDE.md changes are documentation only.

---

## Rollback Notes

**Hook scripts:** Delete the new directory:
```bash
rm -rf '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks'
```

**settings.json:** Restore from git:
```bash
git checkout HEAD -- '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/settings.json'
```

**CLAUDE.md files:** Both are in git. Restore from HEAD:
```bash
git checkout HEAD -- '/Users/kellyl./.claude/CLAUDE.md'
git checkout HEAD -- '/Volumes/Macintosh HD2/Cowork/Projects/costscope/CLAUDE.md'
```

**Emergency unblock (during rollback):** Set `TOKENCAST_SKIP_GATE=1` in environment before running any tool that would be blocked by hooks:
```bash
export TOKENCAST_SKIP_GATE=1
```

No data migrations. No calibration data is affected. No existing scripts are modified.

---

## Open Questions — None

All decisions are made. This plan is ready for implementation without additional context.

### Summary of Key Decisions Made in Final Pass

| Decision | Rationale |
|----------|-----------|
| Keep `$PPID` for marker files | Stable within a session; matches monarch; tests must use isolated TMPDIR |
| All Python via direct pipe (not env var) | Handles multi-line JSON; matches monarch pattern |
| All settings.json entries use `bash '/absolute/path/...'` | Absolute paths work regardless of cwd; handles space in "Macintosh HD2" |
| No `jq` in any hook | macOS does not include jq; python3 is always available |
| `sr-pm` removed from whitelist | No `.claude/agents/sr-pm.md` file exists; whitelist should match agent definitions |
| SKIP_GATE on advisory hooks too | Consistent behavior; prevents noise when bypassing is needed |
| `CALIBRATION_DIR` and `TOKENCAST_SIZE_MARKER` env overrides in estimate-gate | Required for test isolation without touching real calibration files |
| `MARKER_FILE` computed at top of branch-guard | Always available for error messages regardless of code path taken |
