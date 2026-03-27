# Pipeline Enforcement — Implementation Plan

*Date: 2026-03-26*
*Author: Engineer Agent*
*Status: Initial Plan*
*Inputs: pipeline-enforcement-requirements.md, pipeline-enforcement-research.md, pipeline-enforcement-architecture.md, monarch reference hooks*

---

## Overview

Implement a hook-based pipeline enforcement system for tokencast. Phase 1 deploys six hook scripts in a new `.claude/hooks/` directory, updates `.claude/settings.json` to register them, and strengthens both CLAUDE.md files with enforcement references. Phase 2 (scoped here but marked for future implementation) adds `pipeline-state.json` tracking and a state-recorder hook.

All Phase 1 hooks follow the same pattern: `set -euo pipefail` (or no set -e for grep-heavy hooks) + `|| exit 0` fail-open on every fallible command, `TOKENCAST_SKIP_GATE=1` check as the first operation, and `exit 2` (hard block) only from PreToolUse hooks. PostToolUse and UserPromptSubmit hooks are advisory (exit 0 always).

Four of six Phase 1 hooks are ported from the monarch-dashboard reference implementation at `/Users/kellyl./Documents/Cowork Projects/Personal Finance/monarch-dashboard/.claude/hooks/` with adaptations. Two are new.

---

## Changes

### Group A: New Hook Scripts (all independent of each other, can be written in parallel)

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh
Lines: new file
Parallelism: independent
Description: PreToolUse hook. Hard-blocks dispatch of implementation-phase agents (implementer, qa, debugger) when calibration/active-estimate.json is absent or older than 24 hours. Ported from: NEW — no monarch equivalent.
Details:
  - Shebang: #!/usr/bin/env bash
  - No set -e at the top — use || exit 0 per-command instead (prevents false exits from find returning empty)
  - First check: if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then exit 0; fi
  - Read stdin into INPUT variable
  - Extract subagent_type via python3 -c reading json.load(sys.stdin) from INPUT via env var (injection-safe pattern from existing hooks): AGENT_TYPE=$(echo "$INPUT" | python3 -c "import json,sys,os; d=json.loads(os.environ['HOOK_INPUT']); print(d.get('tool_input',{}).get('subagent_type',''))" 2>/dev/null) || AGENT_TYPE=""
    NOTE: Use the env var pattern: export HOOK_INPUT="$INPUT" before the python3 call, then read os.environ['HOOK_INPUT'] inside the script. This matches tokencast's existing injection-safe convention from midcheck.sh.
  - Implementation-phase check: array IMPL_AGENTS=("implementer" "qa" "debugger"). Loop to check if AGENT_TYPE matches. If no match, exit 0 (planning agents pass freely).
  - Size marker check: MARKER_FILE="${TMPDIR:-/tmp}/tokencast-size-${PPID}". If file exists and its content is "XS" or "S", exit 0.
  - Locate CALIBRATION_DIR: SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd); PROJECT_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd); CALIBRATION_DIR="$PROJECT_ROOT/calibration"
  - ESTIMATE_FILE="$CALIBRATION_DIR/active-estimate.json"
  - Existence check: if [ ! -f "$ESTIMATE_FILE" ]; then — output block message to stderr, exit 2
  - Freshness check using find: FRESH=$(find "$ESTIMATE_FILE" -mmin -1440 2>/dev/null || true). If [ -z "$FRESH" ]; then — output stale message to stderr, exit 2
  - Stderr messages (exact text):
    Missing: "BLOCKED: No cost estimate recorded.\nRun /tokencast on the final plan before dispatching implementation agents (implementer, qa, debugger).\nMissing: calibration/active-estimate.json"
    Stale: "BLOCKED: Cost estimate is stale (older than 24 hours).\nRun /tokencast again for the current plan.\nStale file: calibration/active-estimate.json"
  - Exit 0 if all checks pass.
  - Make executable: chmod +x
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/validate-agent-type.sh
Lines: new file
Parallelism: independent
Description: PreToolUse hook. Whitelists allowed agent types — hard-blocks unknown agents. Ported from monarch validate-agent-type.sh with tokencast-specific agent list and SKIP_GATE escape hatch added.
Details:
  - Shebang: #!/usr/bin/env bash
  - No set -e at the top (monarch source doesn't use it; grep pattern varies)
  - First check: if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then exit 0; fi
  - Read stdin into INPUT variable
  - Extract tool_name: TOOL_NAME=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null || true)
  - If TOOL_NAME != "Agent", exit 0
  - Extract subagent_type via same python3 pattern as monarch (jq or python3 inline)
  - ALLOWED list (tokencast agents only — monarch-specific agents removed):
      "pm" "researcher" "architect" "engineer" "implementer" "qa" "debugger"
      "staff-reviewer" "sr-pm" "frontend-designer" "docs-updater" "code-reviewer"
      "explorer" "playwright-qa"
  - Loop: if AGENT_TYPE matches any ALLOWED entry, exit 0
  - If no match: write to stderr: "Blocked: subagent_type=\"${AGENT_TYPE}\" is not a custom agent.\nUse one of the project's custom agents: ${ALLOWED[*]}\nEach has a model assignment in its frontmatter."
  - exit 2
  - Make executable: chmod +x
  - NOTE: sr-pm is added to the list (present in global CLAUDE.md pipeline steps as PP-2.5 but absent from monarch's list)
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/branch-guard.sh
Lines: new file
Parallelism: independent
Description: PreToolUse hook on Bash. Hard-blocks git commit on main (always) and git push without review marker (on any branch). Extends monarch pre-push-gate.sh with: branch detection, commit blocking, SKIP_GATE escape hatch, and tokencast-prefixed marker file.
Details:
  - Shebang: #!/usr/bin/env bash
  - NOTE: Do NOT use set -e — grep returns 1 on no-match which would crash the hook
  - First check: if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then exit 0; fi
  - Read stdin into INPUT variable
  - Extract bash command using same python3 pattern as monarch pre-push-gate.sh (reads tool_input.command)
  - If command is empty, exit 0
  - Strip $(...) subexpressions using the monarch Python stripping logic (character-level depth counter)
  - Strip -m "..." and -m '...' inline commit message args via re.sub
  - Detect git commit: IS_COMMIT — check if stripped command matches (^|[;&|])\s*git\s+commit
  - Detect git push: IS_PUSH — check if stripped command matches (^|[;&|])\s*git\s+push
  - If neither IS_COMMIT nor IS_PUSH, exit 0
  - Get current branch: CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || true)
  - If CURRENT_BRANCH is empty (detached HEAD, bare repo), exit 0 (fail-open)
  - If IS_COMMIT and CURRENT_BRANCH == "main":
      Write to stderr: "BLOCKED: Direct commits to main are not allowed.\nCreate a feature branch first: git checkout -b <feature-name>\nDirect commits to main are prohibited except merge commits from approved PRs."
      exit 2
  - If IS_PUSH:
      MARKER_FILE="${TMPDIR:-/tmp}/tokencast-push-reviewed-${PPID}"
      If marker file exists: rm -f "$MARKER_FILE"; exit 0 (allow push, consume marker)
      Otherwise: write to stderr the push-blocked message (see below), exit 2
  - Push-blocked stderr message:
      "BLOCKED: Push requires completed PR review loop.\nConfirm before pushing:\n  1. All tests pass\n  2. staff-reviewer agent found no remaining comments (clean pass)\n  3. docs-updater has been dispatched\nOnce complete, run:\n  touch ${MARKER_FILE}\nThen re-run the push command."
  - NOTE: git commit on non-main branches is always allowed (only push needs the marker)
  - Make executable: chmod +x
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/inline-edit-guard.sh
Lines: new file
Parallelism: independent
Description: PostToolUse hook on Edit|Write. Warns when the orchestrator (not a sub-agent) edits 3+ unique code files since the last user message. Ported from monarch inline-edit-guard.sh with: agent_type suppression for sub-agents, path filtering for code paths only, SKIP_GATE escape hatch, tokencast-prefixed session dir.
Details:
  - Shebang: #!/usr/bin/env bash
  - NOTE: Do NOT use set -e or set -euo pipefail — grep returns 1 on no-match
  - First check: if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then exit 0; fi
  - Read stdin into INPUT variable
  - Extract agent_type from hook envelope: AGENT_TYPE_CTX=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('agent_type',''))" 2>/dev/null || true)
  - If AGENT_TYPE_CTX is non-empty (hook is firing inside a dispatched sub-agent), exit 0 — sub-agent edits are expected
  - Extract file_path from tool_input: FILE_PATH=$(echo "$INPUT" | python3 ... tool_input.get('file_path',''))
  - If FILE_PATH is empty, exit 0
  - Path filter: only count edits to code paths. Check if FILE_PATH matches any of: /src/, /tests/, /scripts/ (using bash case or grep). If the path does NOT match any code path, exit 0. Allowed-through (non-code) paths: docs/, CLAUDE.md, MEMORY.md, calibration/, references/, .claude/hooks/, .claude/settings, README, ROADMAP, SKILL.md
  - SESSION_DIR="${TMPDIR:-/tmp}/tokencast-unique-files-${PPID}"
  - mkdir -p "$SESSION_DIR"
  - UNIQUE_FILES="$SESSION_DIR/unique_files.txt"
  - touch "$UNIQUE_FILES"
  - Dedup add: if ! grep -qxF "$FILE_PATH" "$UNIQUE_FILES" 2>/dev/null; then echo "$FILE_PATH" >> "$UNIQUE_FILES"; fi
  - COUNT=$(wc -l < "$UNIQUE_FILES" | tr -d ' ')
  - THRESHOLD=3
  - If COUNT >= THRESHOLD:
      FILES=$(awk -F'/' '{print $NF}' "$UNIQUE_FILES" | paste -sd ', ')
      Output warning text (no emoji per project rules):
      "DELEGATION GUARD: You have directly edited ${COUNT} unique code files this task (${FILES}).\nWork touching 3+ files is S/M scope and must be dispatched to an implementer agent.\nXS exception: single file, <5 tool calls total.\nDispatch an implementer or debugger agent instead of editing inline."
  - exit 0 always
  - Make executable: chmod +x
  - NOTE: The counter resets when pipeline-gate.sh deletes unique_files.txt on each UserPromptSubmit
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pre-compact-reminder.sh
Lines: new file
Parallelism: independent
Description: PreCompact hook. Outputs a pipeline state reminder before context compaction. Ported from monarch pre-compact-reminder.sh with tokencast-specific context (active-estimate.json gate, /tokencast skill).
Details:
  - Shebang: #!/usr/bin/env bash
  - No set -e needed — pure heredoc output, no fallible commands
  - No SKIP_GATE check needed — this is an advisory reminder, not a gate. Always output.
  - Output via cat heredoc:
    "CONTEXT COMPACTED — Pipeline enforcement reminder:
    - calibration/active-estimate.json MUST exist before dispatching implementer/qa/debugger agents
    - If the estimate file is missing, run /tokencast on the final plan before proceeding
    - All multi-file work (3+ files) MUST be dispatched to implementer/debugger agents
    - XS exception (inline ok): single file, <5 tool calls total
    - Do NOT edit src/, tests/, or scripts/ files directly if scope is S/M/L
    - If working on an M/L story, verify the full planning pipeline is complete (PP-1 through PP-7)
    - Direct commits to main are blocked — always use a feature branch
    - Resume by re-reading the compaction summary, then dispatch agents as needed"
  - exit 0
  - Make executable: chmod +x
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pipeline-gate.sh
Lines: new file
Parallelism: independent
Description: UserPromptSubmit hook. Injects pipeline classification reminder on every user prompt longer than 20 chars; resets inline-edit-guard counter on each new message. Ported from monarch pipeline-gate.sh with tokencast-specific classification table text and SKIP_GATE handling.
Details:
  - Shebang: #!/bin/bash
  - No set -e at the top (monarch source uses set -e; acceptable here since all operations are fail-safe)
  - No SKIP_GATE check at top — the classification reminder is always useful. But for the counter reset, it's fine to always run.
  - Read stdin into INPUT variable
  - Reset inline-edit-guard counter:
      SESSION_DIR="${TMPDIR:-/tmp}/tokencast-unique-files-${PPID}"
      rm -f "$SESSION_DIR/unique_files.txt"
  - Extract prompt: PROMPT=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('prompt',''))" 2>/dev/null || true)
    PROMPT_LEN=${#PROMPT}
  - Skip for very short prompts (confirmations, "yes", "continue", etc.): if [ "$PROMPT_LEN" -lt 20 ]; then exit 0; fi
  - Output classification reminder via cat heredoc:
    "<pipeline-gate>
    PIPELINE GATE — Before starting work, classify this task:

    | Size | Description                                    | Pipeline Required?             |
    |------|------------------------------------------------|--------------------------------|
    | XS   | Single file, < 5 lines, no tests affected      | No — execute inline            |
    | S    | 1-2 files, clear scope                         | Optional                       |
    | M    | Multi-file, new feature, involves tests        | YES — full pipeline            |
    | L    | New systems, architectural decisions           | YES — full pipeline            |

    If M or L:
    1. State the classification and why
    2. Run the planning pipeline: pm -> researcher -> sr-pm -> architect -> (frontend-designer if UI) -> engineer -> staff-reviewer -> engineer (final)
    3. Run /tokencast on the final plan BEFORE dispatching implementer/qa/debugger
    4. Do NOT skip steps or combine agents — each must be a fresh-context dispatch

    If XS or S:
    1. State the classification briefly
    2. Proceed directly (<5 tool calls may execute inline per CLAUDE.md)

    ENFORCEMENT: estimate-gate.sh will HARD-BLOCK implementer/qa/debugger dispatch if no current active-estimate.json exists.
    ALWAYS state the classification before doing any work.
    </pipeline-gate>"
  - exit 0
  - Make executable: chmod +x
  - NOTE: The <pipeline-gate> XML wrapper differentiates this output from other context in Claude's view
```

---

### Group B: settings.json update (depends on Group A hook files existing)

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/settings.json
Lines: 1-53 (full replacement)
Parallelism: depends-on: Group A (hooks must exist before registering them)
Description: Add six new hook registrations to the existing settings.json. Preserve all existing hooks. Use relative paths (./.claude/hooks/...) for the new enforcement hooks, matching the monarch pattern. Existing hooks use absolute paths with bash wrapper — do not change them.
Details:
  - Preserve all existing entries: Stop/tokencast-learn.sh, PostToolUse/Agent/tokencast-track.sh, PostToolUse/Agent/tokencast-agent-hook.sh, PreToolUse/(no matcher)/tokencast-midcheck.sh, PreToolUse/Agent/tokencast-agent-hook.sh
  - Add PreToolUse Agent matcher entries (append to existing Agent PreToolUse group or add new entry):
      { "matcher": "Agent", "hooks": [{ "type": "command", "command": "./.claude/hooks/estimate-gate.sh" }] }
      { "matcher": "Agent", "hooks": [{ "type": "command", "command": "./.claude/hooks/validate-agent-type.sh" }] }
  - Add PreToolUse Bash matcher entry:
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "./.claude/hooks/branch-guard.sh" }] }
  - Add PostToolUse Edit|Write matcher entry:
      { "matcher": "Edit|Write", "hooks": [{ "type": "command", "command": "./.claude/hooks/inline-edit-guard.sh" }] }
  - Add PreCompact entry:
      { "hooks": [{ "type": "command", "command": "./.claude/hooks/pre-compact-reminder.sh" }] }
  - Add UserPromptSubmit entry:
      { "hooks": [{ "type": "command", "command": "./.claude/hooks/pipeline-gate.sh" }] }
  - Final structure has these top-level hook event keys: Stop, PreToolUse (3 entries: no-matcher midcheck, Agent-matcher for agent-hook, Agent-matcher for estimate-gate, Agent-matcher for validate-agent-type, Bash-matcher for branch-guard), PostToolUse (Agent-matcher for track, Agent-matcher for agent-hook, Edit|Write-matcher for inline-edit-guard), PreCompact, UserPromptSubmit
  - NOTE: The existing PreToolUse no-matcher entry for tokencast-midcheck.sh fires on ALL tools including Agent and Bash — this is correct and unchanged
  - NOTE: Multiple PreToolUse Agent hooks are listed as separate array entries (not merged). Registration order: tokencast-agent-hook.sh fires first (data collection), then estimate-gate.sh, then validate-agent-type.sh. Order matters — if agent-hook.sh fires after estimate-gate.sh blocks, the span start would never be written, which is acceptable.
```

The complete settings.json content:

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
            "command": "./.claude/hooks/estimate-gate.sh"
          }
        ]
      },
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "./.claude/hooks/validate-agent-type.sh"
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "./.claude/hooks/branch-guard.sh"
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
            "command": "./.claude/hooks/inline-edit-guard.sh"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./.claude/hooks/pre-compact-reminder.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "./.claude/hooks/pipeline-gate.sh"
          }
        ]
      }
    ]
  }
}
```

---

### Group C: CLAUDE.md updates (independent of Groups A and B — doc changes)

```
File: /Users/kellyl./.claude/CLAUDE.md
Lines: targeted insertions at specific locations (not full rewrite)
Parallelism: independent
Description: Strengthen three locations in the global CLAUDE.md to reference enforcement hooks and hard consequences.
Details:
  CHANGE 1 — After step 2 ("Confirm approach with user"), insert new step 2a:
    "2a. **Enforcement gate.** For M/L changes, `calibration/active-estimate.json` MUST exist before dispatching implementation-phase agents (`implementer`, `qa`, `debugger`). The `estimate-gate.sh` hook hard-blocks these dispatches when no estimate exists. If blocked, run `/tokencast` on the final plan before proceeding."

  CHANGE 2 — Replace step 3 ("Write tests first") with:
    "3. **Write tests first** — dispatch to `qa` agent. Tests must fail before implementation exists. Cover happy path, edge cases, and error cases. The orchestrator MUST NOT dispatch `implementer` agents until `qa` has been dispatched and tests committed to the feature branch."

  CHANGE 3 — Replace step 8 ("Commit to feature branch") with:
    "8. **Commit to feature branch** — push and create PR against main via `gh pr create`. The `branch-guard.sh` hook hard-blocks `git commit` and `git push` on the `main` branch. Always create a feature branch before committing: `git checkout -b <feature-name>`."

  CHANGE 4 — In the "Agent Delegation — MANDATORY" section, after the Exception line, add:
    "**Hook enforcement:** The `inline-edit-guard.sh` hook warns when the orchestrator edits 3+ unique code files in `src/`, `tests/`, or `scripts/` without dispatching an agent. The `validate-agent-type.sh` hook hard-blocks dispatch of unrecognized agent types. The `estimate-gate.sh` hook hard-blocks implementation agents when `calibration/active-estimate.json` is absent or stale."
    ""
    "**Emergency bypass:** Set `TOKENCAST_SKIP_GATE=1` in the environment to disable all enforcement hooks. Use only for genuine emergencies. Its use is visible in the session transcript."

  NOTE: Steps 10 and 11 (Cost Analysis, Merge) remain unchanged. The review loop in step 9 remains unchanged. Only steps 2, 3, 8, and the delegation section gain enforcement references.
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/CLAUDE.md
Lines: targeted additions
Parallelism: independent
Description: Document the new .claude/hooks/ directory, the six enforcement hooks, and the TOKENCAST_SKIP_GATE escape hatch in the project CLAUDE.md.
Details:
  CHANGE 1 — Add a new "Hook Enforcement" section after the "Key Files" table. The section heading is "## Hook Enforcement" and contains:
    - A brief intro: "Six enforcement hooks in `.claude/hooks/` hard-block the two highest-frequency pipeline violations and inject advisory guardrails for others."
    - A table with columns: Hook | Event | Type | Purpose
      Rows:
        estimate-gate.sh | PreToolUse (Agent) | HARD BLOCK | Blocks implementer/qa/debugger dispatch without active-estimate.json
        validate-agent-type.sh | PreToolUse (Agent) | HARD BLOCK | Blocks unknown agent types
        branch-guard.sh | PreToolUse (Bash) | HARD BLOCK | Blocks git commit on main; blocks git push without review marker
        inline-edit-guard.sh | PostToolUse (Edit/Write) | Advisory | Warns at 3+ unique code files edited directly
        pre-compact-reminder.sh | PreCompact | Advisory | Injects pipeline state reminder before compaction
        pipeline-gate.sh | UserPromptSubmit | Advisory | Injects classification reminder; resets edit counter
    - Emergency bypass note: "Set `TOKENCAST_SKIP_GATE=1` to bypass all gates. Use only for genuine emergencies."
    - Push review gate note: "To allow a push after review loop completes: `touch ${TMPDIR:-/tmp}/tokencast-push-reviewed-$$`"

  CHANGE 2 — In the "Architecture Conventions" section, add a bullet:
    "**Hook placement:** Enforcement hooks live in `.claude/hooks/` (not `scripts/`). Core tokencast functionality remains in `scripts/`. New hooks use relative paths in `settings.json`; existing hooks retain absolute paths."

  CHANGE 3 — In the "Gotchas" section, add a bullet:
    "**Enforcement hooks:** All hooks in `.claude/hooks/` check `TOKENCAST_SKIP_GATE=1` first and exit 0 if set. The inline-edit-guard suppresses warnings when `agent_type` is present in the hook envelope (sub-agent context). The branch-guard uses `|| exit 0` around the `git branch --show-current` call to fail-open in detached HEAD state."
```

---

### Group D: Phase 2 Scaffolding (future — not in Phase 1 delivery, document only)

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pipeline-state-recorder.sh
Lines: new file (Phase 2 — NOT part of Phase 1 delivery)
Parallelism: independent (when implemented)
Description: PostToolUse hook on Agent. Maps agent completions to pipeline-state.json step records. Resolves engineer/staff-reviewer ambiguity using existing steps_completed state. Not implemented in Phase 1.
Details:
  - See architecture document section "Phase 2 Hook Additions" for full specification.
  - File should NOT be created in Phase 1. The settings.json must NOT register it until Phase 2.
```

---

## Dependency Order

```
Phase 1 — can begin immediately:

Round 1 (all parallel):
  A1: .claude/hooks/estimate-gate.sh
  A2: .claude/hooks/validate-agent-type.sh
  A3: .claude/hooks/branch-guard.sh
  A4: .claude/hooks/inline-edit-guard.sh
  A5: .claude/hooks/pre-compact-reminder.sh
  A6: .claude/hooks/pipeline-gate.sh
  C1: ~/.claude/CLAUDE.md changes
  C2: CLAUDE.md (project) changes

Round 2 (after A1-A6 complete — hooks must exist before registration):
  B1: .claude/settings.json update

Phase 2 — after Phase 1 validated (future):
  D1: .claude/hooks/pipeline-state-recorder.sh
  D2: settings.json addition for pipeline-state-recorder.sh
  D3: estimate-gate.sh extension to read pipeline-state.json
  D4: branch-guard.sh extension to check docs_updated and review_loop.status
  D5: pipeline-gate.sh extension to inject pipeline-state.json contents
```

---

## Test Strategy

### Manual Verification Tests (run after implementation)

Each hook can be tested by constructing a synthetic JSON payload and piping it to the hook script.

**T1: estimate-gate.sh — missing estimate (hard block)**
```bash
# Setup: ensure active-estimate.json does not exist
rm -f '/Volumes/Macintosh HD2/Cowork/Projects/costscope/calibration/active-estimate.json'
# Test: pipe an implementer dispatch
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"implementer"}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh'
# Expected: exit 2, stderr contains "BLOCKED: No cost estimate recorded"
```

**T2: estimate-gate.sh — estimate present and fresh (pass)**
```bash
# Setup: create a fresh active-estimate.json
touch '/Volumes/Macintosh HD2/Cowork/Projects/costscope/calibration/active-estimate.json'
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"implementer"}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh'
# Expected: exit 0 (no output, no block)
```

**T3: estimate-gate.sh — planning agent passes without estimate**
```bash
rm -f '/Volumes/Macintosh HD2/Cowork/Projects/costscope/calibration/active-estimate.json'
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"architect"}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh'
# Expected: exit 0 (planning agents not blocked)
```

**T4: estimate-gate.sh — TOKENCAST_SKIP_GATE=1 bypasses block**
```bash
rm -f '/Volumes/Macintosh HD2/Cowork/Projects/costscope/calibration/active-estimate.json'
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"implementer"}}' | \
  TOKENCAST_SKIP_GATE=1 bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh'
# Expected: exit 0 (skip gate bypassed)
```

**T5: estimate-gate.sh — stale estimate (hard block)**
```bash
# Setup: create an old file (mtime >24h ago)
touch -t 202601010000 '/Volumes/Macintosh HD2/Cowork/Projects/costscope/calibration/active-estimate.json'
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"implementer"}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh'
# Expected: exit 2, stderr contains "stale"
```

**T6: estimate-gate.sh — size marker XS bypasses gate**
```bash
rm -f '/Volumes/Macintosh HD2/Cowork/Projects/costscope/calibration/active-estimate.json'
echo "XS" > "${TMPDIR:-/tmp}/tokencast-size-$$"
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"implementer"}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/estimate-gate.sh'
rm -f "${TMPDIR:-/tmp}/tokencast-size-$$"
# Expected: exit 0 (XS bypasses gate)
```

**T7: validate-agent-type.sh — known agent passes**
```bash
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"implementer"}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/validate-agent-type.sh'
# Expected: exit 0
```

**T8: validate-agent-type.sh — unknown agent blocked**
```bash
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"gpt-researcher"}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/validate-agent-type.sh'
# Expected: exit 2, stderr contains "not a custom agent"
```

**T9: branch-guard.sh — commit on main blocked**
```bash
# Run from a branch named "main" (or mock the git command)
echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"fix stuff\""}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/branch-guard.sh'
# Expected: exit 2 if currently on main branch, exit 0 if on feature branch
```

**T10: branch-guard.sh — git commit message containing "git push" is not mistaken for a push**
```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"fix: do not git push on main\""}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/branch-guard.sh'
# Expected: exit 0 on feature branch (commit message stripped correctly), exit 2 on main
```

**T11: branch-guard.sh — push without marker blocked**
```bash
rm -f "${TMPDIR:-/tmp}/tokencast-push-reviewed-${PPID}"
echo '{"tool_name":"Bash","tool_input":{"command":"git push origin HEAD"}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/branch-guard.sh'
# Expected: exit 2, stderr contains "BLOCKED: Push requires completed PR review loop"
```

**T12: branch-guard.sh — push with marker passes and consumes marker**
```bash
MARKER="${TMPDIR:-/tmp}/tokencast-push-reviewed-${PPID}"
touch "$MARKER"
echo '{"tool_name":"Bash","tool_input":{"command":"git push origin HEAD"}}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/branch-guard.sh'
# Expected: exit 0; marker file no longer exists
[ ! -f "$MARKER" ] && echo "PASS: marker consumed" || echo "FAIL: marker still exists"
```

**T13: inline-edit-guard.sh — sub-agent context suppresses warning**
```bash
# Simulate hook firing inside a dispatched sub-agent (agent_type present)
echo '{"tool_name":"Write","tool_input":{"file_path":"/some/path/src/foo.py"},"agent_type":"implementer"}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/inline-edit-guard.sh'
# Expected: exit 0, no warning output (sub-agent edits are expected)
```

**T14: inline-edit-guard.sh — 3 unique code files triggers warning**
```bash
SESSION_DIR="${TMPDIR:-/tmp}/tokencast-unique-files-${PPID}"
rm -rf "$SESSION_DIR"
# Edit 3 different src files
for f in src/a.py src/b.py src/c.py; do
  echo "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/project/$f\"}}" | \
    bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/inline-edit-guard.sh'
done
# Expected: first two calls produce no output; third call outputs "DELEGATION GUARD"
```

**T15: inline-edit-guard.sh — docs/ path not counted**
```bash
SESSION_DIR="${TMPDIR:-/tmp}/tokencast-unique-files-${PPID}"
rm -rf "$SESSION_DIR"
for f in docs/a.md docs/b.md docs/c.md docs/d.md; do
  echo "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/project/$f\"}}" | \
    bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/inline-edit-guard.sh'
done
# Expected: no warning output (docs paths not counted)
```

**T16: pipeline-gate.sh — short prompt suppressed**
```bash
echo '{"prompt":"yes"}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pipeline-gate.sh'
# Expected: no output (prompt < 20 chars)
```

**T17: pipeline-gate.sh — long prompt injects reminder**
```bash
echo '{"prompt":"Please implement the new feature for cost tracking"}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pipeline-gate.sh'
# Expected: output contains "PIPELINE GATE" and classification table
```

**T18: pipeline-gate.sh — resets inline-edit-guard counter**
```bash
SESSION_DIR="${TMPDIR:-/tmp}/tokencast-unique-files-${PPID}"
mkdir -p "$SESSION_DIR"
echo "src/foo.py" > "$SESSION_DIR/unique_files.txt"
echo '{"prompt":"Please implement the new feature for cost tracking"}' | \
  bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pipeline-gate.sh'
[ ! -f "$SESSION_DIR/unique_files.txt" ] && echo "PASS: counter reset" || echo "FAIL: counter not reset"
```

**T19: pre-compact-reminder.sh — outputs reminder**
```bash
echo '' | bash '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/pre-compact-reminder.sh'
# Expected: output contains "CONTEXT COMPACTED" and "active-estimate.json"
```

**T20: All hooks pass TOKENCAST_SKIP_GATE=1**
```bash
for hook in estimate-gate validate-agent-type branch-guard inline-edit-guard pipeline-gate; do
  echo '{"tool_name":"Agent","tool_input":{"subagent_type":"implementer"}}' | \
    TOKENCAST_SKIP_GATE=1 bash "/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/${hook}.sh"
  echo "Hook ${hook} exit: $?"
done
# Expected: all exit 0 (skip gate)
```

### Automated Test File

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_pipeline_enforcement.py
Lines: new file
Parallelism: independent (can be written in parallel with hook implementation — interfaces are defined)
Description: Python test file using subprocess to invoke hooks with synthetic inputs and assert exit codes and output. Covers all 20 manual test cases above plus edge cases.
Details:
  - Use /usr/bin/python3 (not system python3)
  - subprocess.run(["bash", hook_path], input=json_payload, capture_output=True, env={...})
  - Assert returncode == 0 or 2 per test case
  - Assert specific strings in stderr (for block cases)
  - Use tempfile.mkdtemp() for calibration dir isolation in estimate-gate tests
  - Set CALIBRATION_DIR env var override (if hook supports it — see implementation)
  - Test class structure:
      TestEstimateGate (T1-T6)
      TestValidateAgentType (T7-T8)
      TestBranchGuard (T9-T12) — mock git branch via PATH override pointing to a fake git script
      TestInlineEditGuard (T13-T15) — use unique TMPDIR per test to isolate counter files
      TestPipelineGate (T16-T18)
      TestPreCompactReminder (T19)
      TestSkipGate (T20 — all hooks with TOKENCAST_SKIP_GATE=1)
  - TestBranchGuard mocking strategy: create a $TMPDIR/fake-git directory with a script named "git" that outputs "main" for "branch --show-current" and exits 0 for everything else. Set PATH="$TMPDIR/fake-git:$PATH" in the subprocess env.
```

### Existing Tests — Impact Assessment

The six new hook scripts have no Python dependencies and no interaction with the existing tokencast Python modules (`update-factors.py`, `sum-session-tokens.py`, `calibration_store.py`, `parse_last_estimate.py`). The existing test suite (`tests/test_*.py`, 441 tests) is not affected.

`settings.json` changes do not affect the Python test suite (hooks are not invoked during `pytest`).

The CLAUDE.md changes are documentation — no test impact.

---

## Rollback Notes

**Hook scripts:** All new files are in `.claude/hooks/`. To roll back, delete the directory:
```bash
rm -rf '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks'
```

**settings.json:** The file is checked into git. To roll back:
```bash
git checkout HEAD -- '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/settings.json'
```

**CLAUDE.md files:** Both files are version-controlled. To roll back individual changes:
```bash
# Project CLAUDE.md
git checkout HEAD -- '/Volumes/Macintosh HD2/Cowork/Projects/costscope/CLAUDE.md'
# Global CLAUDE.md — not in this repo, requires manual revert
```

**Emergency bypass without rollback:** If a hook is causing problems during a session, set `TOKENCAST_SKIP_GATE=1` in the environment before running Claude Code. All hooks check this variable first and exit 0 immediately. This is the fastest mitigation path.

**Data migration:** No data migration required. The new hooks write only ephemeral state to `$TMPDIR`. No persistent schema changes.

---

## Implementation Notes for the Implementer Agent

### Critical: Path with Spaces

All absolute paths in scripts must be quoted. The repo lives at `/Volumes/Macintosh HD2/Cowork/Projects/costscope` — the space in "Macintosh HD2" will break unquoted paths.

In the hook scripts, derive PROJECT_ROOT safely:
```bash
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
CALIBRATION_DIR="$PROJECT_ROOT/calibration"
```

### Critical: Injection-Safe Python Calls

For reading JSON from stdin in shell scripts, use the env var pattern that tokencast already uses in midcheck.sh. Do NOT do:
```bash
# WRONG — shell interpolation in python args
VAL=$(python3 -c "import json; d=json.loads('$INPUT'); print(d['key'])")
```
Do:
```bash
# CORRECT — env var passes data safely
export HOOK_INPUT="$INPUT"
VAL=$(python3 -c "import json, os; d=json.loads(os.environ['HOOK_INPUT']); print(d.get('tool_input',{}).get('subagent_type',''))" 2>/dev/null || true)
```
Or use process substitution with `echo "$INPUT" | python3 -c "import json,sys; ..."` (reading from sys.stdin) which is also safe since it doesn't interpolate into the Python string.

### Critical: Make All Hooks Executable

After creating each hook file:
```bash
chmod +x '/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/hooks/<hook>.sh'
```

### Critical: settings.json Relative Path Behavior

The monarch settings.json uses `./.claude/hooks/` relative paths. This works because Claude Code runs hooks with the project root as the working directory. Tokencast's existing hooks use absolute paths with the `bash '...'` wrapper. The new hooks should use relative paths (matching monarch) for portability. Both patterns coexist in the same settings.json without conflict.

### PPID vs Session ID

Monarch hooks use `$PPID` for session namespacing (counter dirs, marker files). Tokencast's existing hooks use `session_id` from the hook envelope. The new enforcement hooks use `$PPID` (matching monarch's approach) for the ephemeral `$TMPDIR` files. This is distinct from the calibration directory files which use session_id.

The `$PPID` approach works because: all hook invocations within a single Claude Code session share the same parent process (Claude Code itself), so `$PPID` is stable across tool calls within one session.

### No Edit-Count-Detector

The architecture document does not include `edit-count-detector.sh` (the monarch loop detection hook) in Phase 1. It was analyzed in the research report but not selected for Phase 1 scope. Do NOT implement it — it is not in the Phase 1 deliverable. (The inline-edit-guard provides the delegation signal; the edit-count-detector is a separate concern.)

### Agent Type List Includes sr-pm

The architecture document's validate-agent-type.sh description says to keep the shared set. However, `sr-pm` is listed in the global CLAUDE.md's pipeline steps (PP-2.5) but is absent from monarch's ALLOWED list. Add `sr-pm` to the tokencast allowed list. This is a delta from the monarch reference.

---

## Phase 2 Preview (not in scope for this delivery)

For reference, Phase 2 adds:
- `calibration/pipeline-state.json` schema (orchestrator-written)
- `.claude/hooks/pipeline-state-recorder.sh` — PostToolUse/Agent hook that auto-records step completions
- Extension of `estimate-gate.sh` to also check `pipeline-state.json` when present
- Extension of `branch-guard.sh` to check `review_loop.status` and `docs_updated` before push
- Extension of `pipeline-gate.sh` to inject current `pipeline-state.json` contents into context

Phase 2 is gated on Phase 1 validation: if Phase 1 hooks achieve >95% compliance rate for R1 (estimate gate) and R6 (branch protection), the Phase 2 state file system adds incremental value. If Phase 1 alone proves sufficient, Phase 2 may be deprioritized.
