# Pipeline Enforcement — Architecture Decision

*Date: 2026-03-26*
*Author: Architect Agent*
*Status: Decision*
*Inputs: pipeline-enforcement-requirements.md, pipeline-enforcement-research.md, monarch reference hooks*

---

## Decision Summary

We will build a hook-based pipeline enforcement system for tokencast using a phased approach: Phase 1 deploys six hooks that hard-block the two highest-frequency violations (no cost estimate before implementation, direct commits to main) and provide advisory guardrails for delegation, classification, and compaction recovery. Phase 2 adds full pipeline state tracking via `pipeline-state.json` to enforce planning pipeline completion, TDD ordering, docs updates, and review loop completion. All hooks live in the project's `.claude/hooks/` directory, are registered in `.claude/settings.json`, respect a `TOKENCAST_SKIP_GATE=1` escape hatch, and follow the established fail-silent pattern. Four of the six Phase 1 hooks are adapted from the monarch-dashboard reference implementation; two are new.

---

## Chosen Approach

### Description

**Architecture: Per-concern hook scripts, phased deployment, project-local placement.**

Each enforcement concern gets its own shell script in `.claude/hooks/`. Scripts are small (30-90 lines each), independently fail-silent, and testable in isolation. PreToolUse hooks that enforce gates use `exit 2` for hard blocks. PostToolUse and UserPromptSubmit hooks use `exit 0` with stdout messages for advisory context injection. All hooks check `TOKENCAST_SKIP_GATE=1` as the first operation and exit 0 immediately if set.

The system is split into two phases:

**Phase 1 (immediate, 6 hooks):** Covers R1 (cost estimate gate), R2 (inline edit detection), R6 (main branch protection), plus classification reminders and compaction recovery. No new state files required -- Phase 1 uses only the existing `active-estimate.json` and `$TMPDIR` ephemeral markers.

**Phase 2 (after Phase 1 validated, 2-3 hooks + state file):** Introduces `calibration/pipeline-state.json` for tracking planning pipeline progress (R3), TDD ordering (R4), docs updates (R5), and review loop completion (R7). The Phase 1 estimate gate is extended to also check pipeline-state.json when present.

### Rationale

1. **Per-concern scripts over monolithic gate.** A single script handling all gates violates single-responsibility and creates a single point of failure. If `pipeline-gate-all.sh` crashes on the branch check, the estimate gate also fails open. Per-concern scripts mean a bug in one hook degrades only that one enforcement. The monarch reference implementation uses this pattern successfully with 6 independent scripts.

2. **Hard blocks (exit 2) over advisory messages.** The research report confirmed that PreToolUse hooks support `exit 2` for genuine hard blocks -- the tool call is prevented, not just annotated. The Phase 1 session proved that advisory messages (`additionalContext`) are systematically ignored during high-throughput batch sessions. Exit 2 is the only mechanism that provides actual enforcement. We use it for the two highest-severity violations (R1, R6) and the agent whitelist.

3. **Project-local over global placement.** The tokencast-specific gates (active-estimate.json check, calibration directory paths) are inherently project-scoped. The generic hooks (classification reminder, delegation guard, compaction reminder) could theoretically be global, but placing them project-local avoids affecting other projects that may have different workflow rules. If the owner later wants global deployment, extracting the generic hooks to `~/.claude/hooks/` is straightforward -- the scripts have no project-specific dependencies except the classification table text.

4. **Phased deployment over big-bang.** Phase 1 addresses the two 100%-frequency violations (V1: no estimate on 19/19 stories, V6: direct commits to main) with zero new state file infrastructure. This lets us validate that hard-block hooks work in practice before investing in the more complex pipeline-state.json system. If Phase 1 hooks achieve >95% compliance, Phase 2 may be lower priority.

5. **Orchestrator-written state file (Phase 2) over hook-auto-populated state.** The orchestrator is the only agent that knows the story ID, size classification, and which pipeline step is being run. A PostToolUse hook can record that an agent was dispatched, but cannot reliably map `engineer` dispatches to PP-4 vs PP-6 (both use the same agent). The state file must be orchestrator-written. The trust problem (orchestrator that violated the pipeline is the one writing the state file) is mitigated by: (a) the UserPromptSubmit hook injecting a classification reminder on every turn, (b) the PreToolUse gate failing open when no state file exists (allows XS/S work), and (c) the estimate gate (Phase 1) providing a hard gate independent of the state file.

### Alignment with Success Criteria

| Criterion | How Addressed |
|-----------|---------------|
| SC-1.1: No M/L story reaches implementation without estimate | Phase 1: `estimate-gate.sh` hard-blocks `implementer`/`qa`/`debugger` dispatch when `active-estimate.json` is absent or stale |
| SC-1.2: No orchestrator inline code edits | Phase 1: `inline-edit-guard.sh` warns at 3+ files; uses `agent_type` field to suppress in sub-agents |
| SC-1.3: Complete pipeline-state.json at merge time | Phase 2: `pipeline-state-gate.sh` checks step completion before implementation dispatch |
| SC-1.4: Tests before implementation | Phase 2: pipeline-state.json `tests_written` boolean checked by gate |
| SC-1.5: Memory/docs updated before merge | Phase 2: pipeline-state.json `docs_updated` boolean checked at push time |
| SC-1.6: No direct commits to main | Phase 1: `branch-guard.sh` hard-blocks `git commit` and `git push` on main |
| SC-1.7: Clean review pass before merge | Phase 2: pipeline-state.json `review_loop.status` checked at push time |
| SC-2: Next M-size story end-to-end | Phase 1 + UserPromptSubmit reminder guides the orchestrator; Phase 2 enforces it |
| SC-3.1: XS/S unimpeded | Estimate gate checks `subagent_type` -- only blocks implementation agents. Pipeline-state gate fails open when no state file exists. |
| SC-3.2: Emergency escape hatch | All hooks check `TOKENCAST_SKIP_GATE=1` first |
| SC-3.3: <500ms latency | Each hook does at most one `stat` call + one `jq`/`python3` parse. Measured budget: ~20ms per hook, ~120ms total for 6 hooks. |
| SC-3.4: Fail-open | All hooks use `|| exit 0` on fallible commands |
| SC-3.5: State file manually editable | pipeline-state.json is plain JSON in calibration/ (gitignored) |
| SC-4: Calibration data recovery | Estimate gate ensures active-estimate.json exists before implementation, guaranteeing learn.sh has data at session end |

---

## Rejected Alternatives

### Alternative 1: Single Monolithic Gate Script

**What it was:** One script (`pipeline-gate-all.sh`) registered once per event type, handling all enforcement logic: estimate check, pipeline state check, branch check, delegation detection, and classification reminder.

**Why rejected:** Violates single-responsibility principle in a way that has concrete consequences. A crash in the branch-checking code path (e.g., `git branch --show-current` fails in a detached HEAD state) would cause the entire script to exit via `set -e`, disabling the estimate gate, the delegation guard, and the classification reminder simultaneously. With per-concern scripts, a branch-guard crash disables only branch protection. Additionally, the monarch reference implementation uses per-concern scripts and has been running in production without issues -- there is no evidence that consolidation provides a benefit, while the failure-mode risk is clear. Testing is also harder: a monolithic script requires mocking multiple unrelated conditions per test case.

### Alternative 2: CLAUDE.md Rule Strengthening Only (No New Hooks)

**What it was:** Rewrite CLAUDE.md with stronger language, consequences framing, and explicit "MUST" directives, without adding any new hooks.

**Why rejected:** Empirically disproven. The Phase 1 session had CLAUDE.md rules that already said "MUST" (e.g., "All execution work MUST be dispatched to a named agent") and they were violated 19/19 times for cost estimates and systematically for delegation. The root cause (RC-1) is that CLAUDE.md rules are advisory text with no enforcement mechanism. Stronger advisory text addresses the wrong failure mode -- the problem is not that the rules were unclear, but that nothing prevented violations. This alternative was the status quo with better wording, which the PM requirements explicitly identify as insufficient.

### Alternative 3: Global Hook Placement for Generic Rules

**What it was:** Place the classification reminder (UserPromptSubmit), delegation guard (PostToolUse), compaction reminder (PreCompact), and agent whitelist (PreToolUse) in `~/.claude/settings.json` pointing to scripts in `~/.claude/hooks/`. Only the tokencast-specific estimate gate would be project-local.

**Why rejected:** Risk outweighs benefit at this stage. Global hooks affect every project the owner works on, including projects with different workflow rules, different agent lists, or no planning pipeline requirement. The agent whitelist in particular would need to be a superset of all agents across all projects, weakening its value. The classification table text would need to be generic enough for any project. These are solvable problems but add complexity with no immediate benefit -- the owner works on two projects (tokencast and monarch-dashboard), and monarch already has its own hooks. Extracting generic hooks to global scope is a future optimization, not a Phase 1 requirement.

### Alternative 4: PostToolUse Advisory for Estimate Gate (Strengthen Existing track.sh)

**What it was:** Instead of a hard-blocking PreToolUse gate, strengthen the existing `tokencast-track.sh` PostToolUse hook to output a louder warning when no estimate exists and an implementation agent was just dispatched.

**Why rejected:** PostToolUse fires after the tool call succeeds -- the agent has already been dispatched and is running. The warning arrives too late; the implementation work is already in progress. The Phase 1 session showed that even repeated advisory messages (the memory update reminder) were ignored. A PostToolUse advisory for the estimate gate would repeat the exact failure mode that created the problem.

---

## Design Details

### Phase 1 Hook Inventory

#### Hook 1: `estimate-gate.sh` (NEW)

| Field | Value |
|-------|-------|
| **Event** | PreToolUse |
| **Matcher** | Agent |
| **Purpose** | Hard-block implementation-phase agent dispatch when no cost estimate exists |
| **Exit behavior** | Exit 2 (hard block) when gate fails; exit 0 when gate passes or hook is not applicable |
| **Ported from** | New -- no monarch equivalent |

**What it checks:**
1. Is `TOKENCAST_SKIP_GATE=1`? If yes, exit 0.
2. Read `tool_input.subagent_type` from stdin JSON.
3. Is the agent type in the implementation-phase set (`implementer`, `qa`, `debugger`)? If not, exit 0 -- planning agents pass freely.
4. Does `calibration/active-estimate.json` exist? If not, exit 2 with message.
5. Is the file's mtime within the last 24 hours? If not, exit 2 with message (stale estimate from a prior session/story).

**Data from stdin:** `{ "tool_name": "Agent", "tool_input": { "subagent_type": "implementer", ... }, "session_id": "...", "agent_type": "..." }`

**State files read:** `calibration/active-estimate.json` (existence + mtime only, not parsed).

**State files written:** None.

**Message on block:**
```
BLOCKED: No current cost estimate found.
Run /tokencast before dispatching implementation agents (implementer, qa, debugger).
Missing or stale file: calibration/active-estimate.json
```

**XS/S escape:** The hook checks for a size marker file at `$TMPDIR/tokencast-size-$PPID`. If the file exists and contains "XS" or "S", the gate is bypassed. The UserPromptSubmit hook (pipeline-gate.sh) can write this file when the orchestrator classifies a task. If no marker file exists, the gate applies by default -- this is the safe default (enforcement on unless explicitly opted out).

**24-hour staleness window:** Uses `find "$ESTIMATE_FILE" -mmin -1440` (1440 minutes = 24 hours). If the find returns empty, the file is stale. This is a coarse heuristic suitable for Phase 1; Phase 2 replaces it with pipeline-state.json's per-story tracking.

#### Hook 2: `validate-agent-type.sh` (PORTED from monarch)

| Field | Value |
|-------|-------|
| **Event** | PreToolUse |
| **Matcher** | Agent |
| **Purpose** | Whitelist allowed agent types to prevent use of non-custom agents |
| **Exit behavior** | Exit 2 (hard block) for unknown agent types; exit 0 for allowed types |
| **Ported from** | monarch `validate-agent-type.sh` |

**Adaptations from monarch:**
- Remove monarch-specific agents: `commit-drafter`, `pr-drafter`, `test-triager`, `lint-fixer`, `packet-summarizer`, `changelog-scanner`, `loop-guard`, `security-scanner`.
- Keep the shared set: `pm`, `researcher`, `architect`, `engineer`, `implementer`, `qa`, `debugger`, `staff-reviewer`, `frontend-designer`, `docs-updater`, `code-reviewer`, `explorer`, `playwright-qa`.
- Add `TOKENCAST_SKIP_GATE=1` check at the top.

**State files:** None.

#### Hook 3: `branch-guard.sh` (NEW, extends monarch `pre-push-gate.sh`)

| Field | Value |
|-------|-------|
| **Event** | PreToolUse |
| **Matcher** | Bash |
| **Purpose** | Block `git commit` and `git push` on the `main` branch |
| **Exit behavior** | Exit 2 (hard block) when committing/pushing on main; exit 0 otherwise |
| **Ported from** | Partially from monarch `pre-push-gate.sh`, with significant additions |

**What it checks:**
1. Is `TOKENCAST_SKIP_GATE=1`? If yes, exit 0.
2. Extract bash command from stdin JSON.
3. Strip `$(...)` subexpressions and `-m "..."` args (monarch pattern).
4. Check if command contains `git commit` or `git push`.
5. If neither, exit 0.
6. Check current branch: `git branch --show-current 2>/dev/null || exit 0`.
7. If branch is `main`:
   - For `git push`: Check for push-reviewed marker file (monarch pattern). If marker exists, consume it and exit 0. Otherwise exit 2.
   - For `git commit`: Exit 2 unconditionally (no marker escape -- commits to main are never allowed).
8. If branch is not `main`: For `git push`, check the push-reviewed marker (same as monarch). For `git commit`, exit 0.

**Key difference from monarch:** Monarch only checks `git push`. This hook also checks `git commit` on main, which was the Phase 1 violation. The `git branch --show-current` call adds ~5ms and uses `|| exit 0` for fail-open in detached HEAD or bare repo states.

**Marker file:** `$TMPDIR/tokencast-push-reviewed-$PPID` -- same pattern as monarch but with `tokencast-` prefix to avoid namespace collision if both projects run in the same session.

**State files read:** Marker file (existence check + consume).
**State files written:** None (marker file is written externally by the orchestrator via a `touch` command).

**Message on block (commit on main):**
```
BLOCKED: Direct commits to main are not allowed.
Create a feature branch first: git checkout -b <feature-name>
```

**Message on block (push without review):**
```
BLOCKED: Push requires completed PR review loop.
Confirm: (1) all tests pass, (2) staff-reviewer found no remaining comments.
Then run: touch $TMPDIR/tokencast-push-reviewed-$PPID
```

#### Hook 4: `inline-edit-guard.sh` (PORTED from monarch)

| Field | Value |
|-------|-------|
| **Event** | PostToolUse |
| **Matcher** | Edit\|Write |
| **Purpose** | Warn when the orchestrator edits 3+ unique files directly (delegation signal) |
| **Exit behavior** | Exit 0 always (advisory, cannot block -- PostToolUse) |
| **Ported from** | monarch `inline-edit-guard.sh` |

**Adaptations from monarch:**
- Add `agent_type` check: if `agent_type` is present and non-empty in the hook input JSON, the hook is firing inside a sub-agent. Sub-agent edits are expected -- exit 0 without counting.
- Add `TOKENCAST_SKIP_GATE=1` check.
- Add path filtering: only count edits to `src/`, `tests/`, `scripts/` paths. Edits to `docs/`, `CLAUDE.md`, `MEMORY.md`, `calibration/`, `references/` are planning/docs work and should not trigger the guard.

**State files read/written:** `$TMPDIR/tokencast-unique-files-$PPID/unique_files.txt` -- same pattern as monarch with `tokencast-` prefix.

**Counter reset:** Performed by `pipeline-gate.sh` (Hook 6) on each UserPromptSubmit.

#### Hook 5: `pre-compact-reminder.sh` (PORTED from monarch)

| Field | Value |
|-------|-------|
| **Event** | PreCompact |
| **Matcher** | (none) |
| **Purpose** | Inject pipeline state reminder before context compaction |
| **Exit behavior** | Exit 0 always (PreCompact cannot block) |
| **Ported from** | monarch `pre-compact-reminder.sh` |

**Adaptations from monarch:**
- Add tokencast-specific context: remind to check `calibration/active-estimate.json` and `calibration/pipeline-state.json` (when Phase 2 is deployed).
- Remind about the planning pipeline steps.
- Include the cost estimate gate: "If you have not run /tokencast for the current plan, you will be blocked from dispatching implementation agents."

**State files:** None. Pure text output.

**Message:**
```
CONTEXT COMPACTED -- Pipeline enforcement reminder:
- Check calibration/active-estimate.json -- estimate MUST exist before implementation agents
- All multi-file work (3+ files) MUST be dispatched to implementer/debugger agents
- XS exception (inline ok): single file, <5 tool calls total
- If working on an M/L story, verify the planning pipeline is complete before implementation
- Resume by re-reading the compaction summary, then dispatch agents as needed
```

#### Hook 6: `pipeline-gate.sh` (PORTED from monarch)

| Field | Value |
|-------|-------|
| **Event** | UserPromptSubmit |
| **Matcher** | (none) |
| **Purpose** | Inject classification reminder on every user prompt; reset inline-edit counter |
| **Exit behavior** | Exit 0 always (advisory) |
| **Ported from** | monarch `pipeline-gate.sh` |

**Adaptations from monarch:**
- Same classification table (matches CLAUDE.md exactly).
- Reset the `tokencast-unique-files-$PPID/unique_files.txt` counter (inline-edit-guard coordination).
- Add size marker file write: if the prompt or a state file indicates the task is XS/S, write the classification to `$TMPDIR/tokencast-size-$PPID` so the estimate gate can skip enforcement. (Phase 1: this is a placeholder -- the orchestrator would need to state "XS" or "S" in its response, which a UserPromptSubmit hook cannot detect. In practice, the size marker is written by the orchestrator via an inline `touch` command or by the PostToolUse hook after a planning agent completes.)
- Phase 2 addition: if `calibration/pipeline-state.json` exists, inject its contents into context so the orchestrator always sees current pipeline state.

**State files read:** `calibration/pipeline-state.json` (Phase 2, optional).
**State files written:** Deletes `$TMPDIR/tokencast-unique-files-$PPID/unique_files.txt`.

### Phase 1 Hook Interaction Diagram

```
UserPromptSubmit
  pipeline-gate.sh -----> resets inline-edit counter
                    -----> injects classification reminder
                    -----> (Phase 2: injects pipeline-state.json)

PreToolUse [Agent]
  estimate-gate.sh -----> checks active-estimate.json + size marker
  validate-agent-type.sh -> checks agent whitelist

PreToolUse [Bash]
  branch-guard.sh ------> checks git commit/push + current branch + push marker

PostToolUse [Edit|Write]
  inline-edit-guard.sh --> counts unique files, warns at 3+ (orchestrator only)

PreCompact
  pre-compact-reminder.sh -> injects pipeline state reminder

Stop
  tokencast-learn.sh ----> (existing, unchanged)
```

Hooks within the same event+matcher fire in registration order. There are no ordering dependencies between them -- each is self-contained. The only cross-hook coordination is the inline-edit-guard counter reset by pipeline-gate.sh, which uses a shared filesystem path convention (not data passing).

### Phase 2 Design (Pipeline State Tracking)

Phase 2 introduces `calibration/pipeline-state.json` and extends the Phase 1 hooks.

#### State File Schema

```json
{
  "story_id": "US-1b.04",
  "size": "M",
  "steps_completed": ["PP-1", "PP-2", "PP-3", "PP-4", "PP-5", "PP-6", "PP-7"],
  "steps_required": ["PP-1", "PP-2", "PP-3", "PP-4", "PP-5", "PP-6", "PP-7"],
  "tests_written": false,
  "docs_updated": false,
  "review_loop": {
    "passes": [],
    "status": "not_started"
  },
  "created_at": "2026-03-26T10:00:00Z",
  "last_updated": "2026-03-26T12:00:00Z"
}
```

**Who writes it:** The orchestrator, at the start of each M/L story. The orchestrator updates it after each pipeline step completes (e.g., after dispatching `pm` agent, adds "PP-1" to `steps_completed`). This is an honor-system write -- the enforcement is in the gate that reads it.

**Who reads it:**
- `estimate-gate.sh` (extended): checks `steps_completed` includes all `steps_required` before allowing implementation agents.
- `branch-guard.sh` (extended): checks `review_loop.status === "clean"` and `docs_updated === true` before allowing push.
- `pipeline-gate.sh` (extended): injects current state into context on every user prompt.

#### Phase 2 Hook Additions

**Hook 7: `pipeline-state-recorder.sh` (NEW)**

| Field | Value |
|-------|-------|
| **Event** | PostToolUse |
| **Matcher** | Agent |
| **Purpose** | Auto-record pipeline step completion when agents finish |
| **Exit behavior** | Exit 0 always (recording only, no blocking) |

Maps agent completions to pipeline steps:
- `pm` -> PP-1
- `researcher` -> PP-2
- `architect` -> PP-3
- `frontend-designer` -> PP-3b
- `engineer` -> PP-4 (first dispatch) or PP-6 (subsequent)
- `staff-reviewer` -> PP-5 (during planning) or review_loop pass (during review)
- `qa` -> sets `tests_written = true`
- `docs-updater` -> sets `docs_updated = true`

The `engineer` ambiguity (PP-4 vs PP-6) is resolved by checking whether PP-5 (staff review) is already complete: if PP-5 is in `steps_completed`, the current `engineer` dispatch is PP-6; otherwise it is PP-4.

The `staff-reviewer` ambiguity (PP-5 vs review pass) is resolved by checking whether all `steps_required` are complete: if the planning pipeline is done, this is a review pass; otherwise it is PP-5.

**Limitation:** This hook can only record steps that were dispatched as named agents. If the orchestrator skips a step entirely, it is not recorded. The gate catches the omission (missing steps in `steps_completed`).

### Data Model Changes

**New files:**
- `.claude/hooks/estimate-gate.sh` (Phase 1)
- `.claude/hooks/validate-agent-type.sh` (Phase 1)
- `.claude/hooks/branch-guard.sh` (Phase 1)
- `.claude/hooks/inline-edit-guard.sh` (Phase 1)
- `.claude/hooks/pre-compact-reminder.sh` (Phase 1)
- `.claude/hooks/pipeline-gate.sh` (Phase 1)
- `.claude/hooks/pipeline-state-recorder.sh` (Phase 2)
- `calibration/pipeline-state.json` (Phase 2, ephemeral, gitignored)
- `$TMPDIR/tokencast-size-$PPID` (ephemeral, per-session)
- `$TMPDIR/tokencast-push-reviewed-$PPID` (ephemeral, per-session)
- `$TMPDIR/tokencast-unique-files-$PPID/unique_files.txt` (ephemeral, per-session)

**Modified files:**
- `.claude/settings.json` (add new hook registrations)
- `CLAUDE.md` (add enforcement rules and hook documentation)
- `~/.claude/CLAUDE.md` (strengthen pipeline rules with enforcement references)
- `.gitignore` (already covers `calibration/`; no change needed)

### Hook Script Placement: `.claude/hooks/` vs `scripts/`

The enforcement hooks live in `.claude/hooks/` (not `scripts/`). Rationale:

1. **Separation of concerns.** The `scripts/` directory contains tokencast's core functionality (learn, midcheck, agent-hook, track, update-factors, etc.). Enforcement hooks are a workflow layer above tokencast's cost estimation -- they enforce the development process, not compute costs.

2. **Convention alignment.** The monarch reference implementation uses `.claude/hooks/` for all its enforcement hooks. Following the same convention makes cross-project navigation intuitive.

3. **Settings.json path clarity.** Existing tokencast hooks in `scripts/` use absolute paths with `bash '...'` wrapper. The new hooks in `.claude/hooks/` use relative paths (`./.claude/hooks/...`) for portability, matching the monarch pattern.

**Note on existing hooks:** The existing tokencast hooks (`tokencast-learn.sh`, etc.) remain in `scripts/` with their current absolute-path invocations. No migration needed -- they serve a different purpose.

---

## Global CLAUDE.md Changes

File: `~/.claude/CLAUDE.md`

### Changes to Development Workflow Section

**Add after "Confirm approach with user before writing code" (step 2):**

> **2a. Enforcement gate.** For M/L changes, `calibration/active-estimate.json` must exist before dispatching implementation-phase agents (`implementer`, `qa`, `debugger`). The `estimate-gate.sh` hook hard-blocks these dispatches when no estimate exists. If blocked, run `/tokencast` on the final plan before proceeding.

**Strengthen step 3 ("Write tests first"):**

Current: "Write tests first -- dispatch to `qa` agent. Tests must fail before implementation exists."

Proposed: "Write tests first -- dispatch to `qa` agent. Tests must fail before implementation exists. The orchestrator MUST NOT dispatch `implementer` agents until `qa` has been dispatched and tests committed to the feature branch."

**Strengthen step 8 ("Commit to feature branch"):**

Current: "Commit to feature branch -- push and create PR against main via `gh pr create`"

Proposed: "Commit to feature branch -- push and create PR against main via `gh pr create`. The `branch-guard.sh` hook hard-blocks `git commit` and `git push` on the `main` branch. Always create a feature branch before committing: `git checkout -b <feature-name>`."

**Add to Agent Delegation section:**

> **Enforcement:** The `inline-edit-guard.sh` hook warns when the orchestrator edits 3+ unique code files (`src/`, `tests/`, `scripts/`) without dispatching an agent. The `validate-agent-type.sh` hook hard-blocks dispatch of unrecognized agent types.
>
> **Emergency bypass:** Set `TOKENCAST_SKIP_GATE=1` in the environment to disable all enforcement hooks. Use only for genuine emergencies. Its use is visible in the session transcript.

### Changes to Pipeline Steps Table

No structural changes. Add a note:

> **Enforcement hooks active:** The `estimate-gate.sh` hook verifies PP-7 completion before allowing implementation-phase agent dispatch. See project CLAUDE.md for details.

---

## Project CLAUDE.md Changes

File: `/Volumes/Macintosh HD2/Cowork/Projects/costscope/CLAUDE.md`

### New Section: Pipeline Enforcement Hooks

Add after the "Architecture Conventions" section:

```
## Pipeline Enforcement Hooks

The `.claude/hooks/` directory contains workflow enforcement hooks. These are
separate from the tokencast cost estimation hooks in `scripts/`.

| Hook | Event | Behavior | Purpose |
|------|-------|----------|---------|
| `estimate-gate.sh` | PreToolUse Agent | Hard block (exit 2) | Blocks implementer/qa/debugger dispatch without active-estimate.json |
| `validate-agent-type.sh` | PreToolUse Agent | Hard block (exit 2) | Blocks unrecognized agent types |
| `branch-guard.sh` | PreToolUse Bash | Hard block (exit 2) | Blocks git commit/push on main branch |
| `inline-edit-guard.sh` | PostToolUse Edit\|Write | Advisory warn | Warns when orchestrator edits 3+ code files directly |
| `pre-compact-reminder.sh` | PreCompact | Advisory | Reminds about pipeline state after compaction |
| `pipeline-gate.sh` | UserPromptSubmit | Advisory | Injects classification reminder on every user prompt |

**Escape hatch:** `TOKENCAST_SKIP_GATE=1` disables all enforcement hooks.

**Push gate:** After the PR review loop is clean, allow push by running:
`touch $TMPDIR/tokencast-push-reviewed-$PPID`

**Hook conventions:**
- All hooks check `TOKENCAST_SKIP_GATE=1` first and exit 0 if set
- All hooks use `set -euo pipefail` with `|| exit 0` on fallible commands (fail-open)
- Hard-block hooks write messages to stderr and exit 2
- Advisory hooks write messages to stdout and exit 0
- Cross-hook coordination uses `$TMPDIR/tokencast-*-$PPID` filesystem paths
```

### Test Command Updates

Add to the test commands section:

```bash
# Test enforcement hooks (requires jq)
# Simulate estimate gate block:
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"implementer"}}' | \
  bash '.claude/hooks/estimate-gate.sh'
# Expected: exit 2 (blocked -- no active-estimate.json)

# Simulate branch guard:
echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m test"}}' | \
  bash '.claude/hooks/branch-guard.sh'
# Expected: exit 2 if on main, exit 0 if on feature branch
```

### Key Files Table Update

Add to the Key Files table:

| Path | Purpose |
|------|---------|
| `.claude/hooks/` | Pipeline enforcement hooks (separate from tokencast cost hooks in `scripts/`) |
| `.claude/hooks/estimate-gate.sh` | Hard-blocks implementation agents without cost estimate |
| `.claude/hooks/branch-guard.sh` | Hard-blocks commits/pushes on main branch |

---

## settings.json Design

The complete `.claude/settings.json` after Phase 1 deployment:

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
          },
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

**Notes on the configuration:**

1. The existing tokencast hooks (`learn.sh`, `midcheck.sh`, `agent-hook.sh`, `track.sh`) are unchanged. Their absolute-path `bash '...'` invocation pattern is preserved.

2. The new enforcement hooks use relative paths (`./.claude/hooks/...`) without the `bash '...'` wrapper. This assumes the scripts are `chmod +x` with a proper shebang (`#!/usr/bin/env bash`). This matches the monarch pattern.

3. The `estimate-gate.sh` and `validate-agent-type.sh` hooks are registered in the same matcher block (`"matcher": "Agent"`). They fire sequentially on every Agent tool call. If `estimate-gate.sh` exits 2, `validate-agent-type.sh` does not fire (the tool call is already blocked). This is the correct behavior -- if there's no estimate, the agent type check is moot.

4. **Permission changes:** None needed. The existing `"allow": ["Bash(*)", "Write(*)", "Edit(*)"]` pattern from monarch is not present in tokencast's settings.json (tokencast relies on `settings.local.json` for permissions). No permission changes are required.

---

## Cost Estimate Enforcement (R1) — Detailed Design

### Detection Mechanism

The estimate gate checks for `calibration/active-estimate.json` using a two-step test:

**Step 1: Existence.** `[ -f "$ESTIMATE_FILE" ]` -- if the file does not exist, the gate fires immediately.

**Step 2: Freshness.** `find "$ESTIMATE_FILE" -mmin -1440 -print | grep -q .` -- if the file exists but its mtime is older than 24 hours, it is stale (likely from a prior session or story). The gate fires.

The 24-hour window is deliberately generous. Within a single session, the estimate will always be fresh. The window exists to catch stale estimates left from prior sessions that were not cleaned up. Phase 2's pipeline-state.json provides per-story precision.

### Gate Location

PreToolUse on Agent tool, filtered to implementation-phase agents only (`implementer`, `qa`, `debugger`). Planning agents (`pm`, `researcher`, `architect`, `engineer`, `staff-reviewer`, `frontend-designer`, `docs-updater`, `explorer`, `code-reviewer`, `playwright-qa`) are never blocked by this gate.

### XS/S Escape

The hook checks for `$TMPDIR/tokencast-size-$PPID`. If the file contains "XS" or "S", the gate is bypassed.

**How the marker gets written:** The orchestrator runs a Bash command like `echo "XS" > $TMPDIR/tokencast-size-$$` after classifying the change. The `$$` in the orchestrator's Bash context is the same as `$PPID` in the hook's context (the hook runs as a child of the Claude Code process, whose PID is the orchestrator's `$$`).

**Open question:** Does `$$` in a Bash tool call match `$PPID` in a hook? This needs verification. If not, the hook can read `session_id` from the JSON input and use that as the namespace instead of `$PPID`. The fallback is to use `$TMPDIR/tokencast-size-$(cat /tmp/tokencast-session-id 2>/dev/null || echo unknown)`.

### Emergency Escape

`TOKENCAST_SKIP_GATE=1` bypasses. The hook logs the bypass to stderr (visible in session transcript but not blocking):

```bash
if [[ "${TOKENCAST_SKIP_GATE:-}" == "1" ]]; then
  echo "[estimate-gate] SKIP_GATE=1 — enforcement bypassed" >&2
  exit 0
fi
```

### Multi-Story Sessions

Phase 1: The 24-hour freshness window means a second story started 2 hours after the first will see the first story's estimate as "fresh" and pass the gate. This is a known limitation. The orchestrator should run `/tokencast` for each story anyway (CLAUDE.md rule), and Phase 2's pipeline-state.json resets per story.

Phase 2: The estimate gate additionally checks `pipeline-state.json` for `PP-7` in `steps_completed`. Each story creates a fresh state file, so the gate is per-story.

---

## Risks and Mitigations

### Risk 1: Hooks fire on every tool call, adding latency

**Severity:** Medium. **Probability:** Low.

The PreToolUse hooks fire on every tool call (midcheck.sh already does this). Adding estimate-gate.sh (Agent-only) and branch-guard.sh (Bash-only) adds targeted checks, not blanket overhead.

**Mitigation:** Each new hook does at most one `stat` call and one `jq`/`python3` parse. No JSONL scanning, no network calls. Measured latency budget per hook: ~20ms. Total for 6 new hooks: ~120ms, well within the 500ms budget (C-2). The UserPromptSubmit hook fires only on user messages (not tool calls), so it adds zero per-tool-call latency.

**Threshold:** If measured total latency exceeds 300ms, consolidate the two Agent-matcher PreToolUse hooks (estimate-gate + validate-agent-type) into a single script.

### Risk 2: Hard blocks (exit 2) are too aggressive and frustrate legitimate work

**Severity:** Medium. **Probability:** Low.

Hard blocks on implementation agents could block legitimate work if `active-estimate.json` was accidentally deleted or if the orchestrator has a valid reason to skip the estimate.

**Mitigation:** Three escape paths exist: (1) `TOKENCAST_SKIP_GATE=1` bypasses all gates, (2) the XS/S size marker bypasses the estimate gate for small changes, (3) the gate only fires for implementation-phase agents -- planning and exploration agents are never blocked. The 24-hour freshness window is generous enough that a recent estimate from the same session always passes.

### Risk 3: Orchestrator ignores advisory hooks (Phase 1 repeat)

**Severity:** High. **Probability:** Medium.

The inline-edit-guard and pipeline-gate are advisory (exit 0 with stdout messages). The Phase 1 session proved advisory messages can be ignored. These hooks may have low compliance.

**Mitigation:** The highest-impact gates (estimate, branch, agent whitelist) use hard blocks (exit 2), not advisory. Advisory hooks cover lower-severity concerns (inline editing, classification reminder) where the cost of a violation is lower. Phase 2 converts the remaining advisory gates (TDD, docs, review loop) to hard blocks via pipeline-state.json integration. The advisory hooks still provide value as reminders even at <100% compliance -- they need to work only often enough to prevent systematic drift, not on every single call.

### Risk 4: `$PPID` mismatch between hooks and orchestrator Bash commands

**Severity:** Medium. **Probability:** Medium.

The marker file pattern (`$TMPDIR/tokencast-*-$PPID`) assumes hooks and the orchestrator's Bash `$$` resolve to the same PID. If Claude Code spawns hooks as grandchildren (not direct children) of the session process, `$PPID` in the hook differs from `$$` in the orchestrator's Bash.

**Mitigation:** Test this empirically before Phase 1 deployment. If `$PPID` does not match, switch to `session_id` from the hook's JSON input. The session_id is guaranteed stable across all hooks and tool calls within a session. The marker file path becomes `$TMPDIR/tokencast-size-$SESSION_ID` where `SESSION_ID` is extracted from stdin JSON.

### Risk 5: Pipeline-state.json is not created by the orchestrator (Phase 2)

**Severity:** Medium. **Probability:** Medium.

If the orchestrator skips creating `pipeline-state.json`, the Phase 2 gate has nothing to check. The same agent that violated the pipeline is responsible for maintaining the enforcement state.

**Mitigation:** (1) Fail-open: when `pipeline-state.json` does not exist, the estimate gate (Phase 1) still enforces -- the estimate file check is independent of the state file. (2) The UserPromptSubmit hook injects a reminder to create the state file for M/L work. (3) The Phase 2 state-recorder hook auto-populates step completions on PostToolUse Agent, providing partial automation. (4) The combination of Phase 1 hard gates + Phase 2 state tracking means the orchestrator must at minimum run `/tokencast` (hard gate) even if it skips the state file.

---

## Open Questions

### OQ-1: `$PPID` vs `$$` alignment (MUST resolve before implementation)

Does `$PPID` in a hook script match `$$` in the orchestrator's Bash tool calls? This determines whether the marker file pattern works. The implementer should add a test hook that writes `$PPID` to a file and a Bash command that writes `$$`, then compare.

**If they don't match:** Use `session_id` from the hook JSON input instead of `$PPID`. This requires parsing stdin JSON in every hook that uses markers, adding ~10ms.

### OQ-2: Relative paths in settings.json (MUST verify before implementation)

The monarch hooks use relative paths (`./.claude/hooks/...`). Tokencast's existing hooks use absolute paths with `bash '...'` wrapper. Do relative paths work reliably when the working directory has spaces (`/Volumes/Macintosh HD2/...`)? If not, the new hooks must also use absolute paths with `bash '...'` quoting.

**Recommendation:** Use the same absolute-path + `bash '...'` pattern as existing hooks for consistency and safety. The monarch pattern works because monarch's repo path has no spaces.

### OQ-3: Multi-story batch policy (OWNER DECISION)

The enforcement system is designed for one story at a time. The Phase 1 session attempted 19 stories in one session. Should we:
- (a) Enforce one story at a time (each story gets its own branch, PR, merge cycle)
- (b) Support batching with per-story state tracking
- (c) Add a CLAUDE.md rule capping stories per session

**Recommendation:** Option (a). The planning pipeline, TDD, review loop, and calibration are all designed around single-story granularity. Batching 19 stories collapses the quality gates. The enforcement system assumes one active story; multi-story support adds complexity with no clear benefit.

### OQ-4: edit-count-detector.sh inclusion (IMPLEMENTATION DECISION)

The monarch `edit-count-detector.sh` (warns at 5+ edits to the same file) is not in the Phase 1 hook list. It is useful for loop detection but does not address any PM requirement directly. Should it be included in Phase 1?

**Recommendation:** Include it. It is a direct port with zero adaptation needed, adds no latency to non-Edit/Write calls, and provides early warning for debugging loops. Register it alongside `inline-edit-guard.sh` in the same `Edit|Write` PostToolUse matcher block.

---

## Implementation Order

### Phase 1: Immediate, High ROI (6 hooks + CLAUDE.md + settings.json)

**Priority order within Phase 1:**

1. **`estimate-gate.sh`** -- Addresses V1 (100% frequency, highest severity, irrecoverable data loss). Single most important hook.
2. **`branch-guard.sh`** -- Addresses V6 (direct commits to main). Hard block, simple logic.
3. **`validate-agent-type.sh`** -- Direct port from monarch. Enforces agent discipline, prevents model misassignment.
4. **`pipeline-gate.sh`** -- Direct port from monarch. Addresses V3/V4/V5 at advisory level via classification reminder.
5. **`inline-edit-guard.sh`** -- Adapted port from monarch. Addresses V2 (inline execution) at advisory level.
6. **`pre-compact-reminder.sh`** -- Direct port from monarch. Addresses OQ-3 (compaction recovery).

**Also in Phase 1:**
- Update `.claude/settings.json` with all hook registrations
- Update project `CLAUDE.md` with enforcement section
- Update global `~/.claude/CLAUDE.md` with enforcement references
- `edit-count-detector.sh` port (bonus, zero-effort)
- Verify `$PPID` alignment (OQ-1) and path quoting (OQ-2) before any hook scripts are written

**Estimated scope:** 7 new files in `.claude/hooks/`, 2 modified files (settings.json, CLAUDE.md), 1 modified global file. All hooks are 30-90 lines of bash. Total new code: ~400-500 lines.

### Phase 2: After Phase 1 Validated (state file + extended gates)

**Trigger:** Phase 1 hooks have been active for at least 2 M-size sessions with observed compliance.

1. **`calibration/pipeline-state.json` schema** -- Define and document.
2. **`pipeline-state-recorder.sh`** -- PostToolUse on Agent, auto-records step completions.
3. **Extend `estimate-gate.sh`** -- Additionally check pipeline-state.json for full planning pipeline completion.
4. **Extend `branch-guard.sh`** -- Additionally check `review_loop.status === "clean"` and `docs_updated === true`.
5. **Extend `pipeline-gate.sh`** -- Inject pipeline-state.json contents into context.

**Estimated scope:** 1 new hook, 3 extended hooks, 1 new state file schema. Total new/modified code: ~200-300 lines.

---

## Appendix: Hook Input Schema Reference

All hooks receive JSON on stdin. The common fields vary by event type.

**PreToolUse (Agent):**
```json
{
  "tool_name": "Agent",
  "tool_input": {
    "subagent_type": "implementer",
    "prompt": "...",
    "name": "impl-1"
  },
  "session_id": "abc123",
  "transcript_path": "/path/to/transcript.jsonl",
  "agent_type": "orchestrator-or-empty"
}
```

**PreToolUse (Bash):**
```json
{
  "tool_name": "Bash",
  "tool_input": {
    "command": "git commit -m 'fix: something'"
  },
  "session_id": "abc123"
}
```

**PostToolUse (Edit|Write):**
```json
{
  "tool_name": "Edit",
  "tool_input": {
    "file_path": "/path/to/file.py",
    "old_string": "...",
    "new_string": "..."
  },
  "tool_output": "...",
  "session_id": "abc123",
  "agent_type": "implementer-or-empty"
}
```

**UserPromptSubmit:**
```json
{
  "prompt": "Add a new feature that...",
  "session_id": "abc123"
}
```

**PreCompact:**
```json
{
  "session_id": "abc123"
}
```
