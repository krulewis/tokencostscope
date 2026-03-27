# Pipeline Enforcement — Research Report

*Date: 2026-03-26*
*Author: Research Agent*
*Input: pipeline-enforcement-requirements.md*

---

## Problem Summary

Tokencast's development pipeline (CLAUDE.md) was violated seven ways in the Phase 1 session: no cost estimates before implementation, orchestrator writing code inline instead of delegating, skipped planning pipeline, tests written after code, memory/docs not updated, direct commits to main, and incomplete PR review loop. The violations were systematic and went unchecked because all existing hooks are advisory. The PM requirements ask for a hook-based enforcement system covering all seven violations, implemented using Claude Code's hook API, without new external dependencies, and with a fail-open escape hatch.

---

## Codebase Context

### Existing Hooks in Tokencast (.claude/settings.json)

The tokencast project already registers five hooks:

| Event | Matcher | Script | Purpose |
|-------|---------|--------|---------|
| Stop | (none) | `tokencast-learn.sh` | Record session actuals |
| PreToolUse | (none, fires on all tools) | `tokencast-midcheck.sh` | Mid-session cost warnings |
| PreToolUse | Agent | `tokencast-agent-hook.sh` | Write span start to sidecar JSONL |
| PostToolUse | Agent | `tokencast-track.sh` | Nudge to run /tokencast after plans |
| PostToolUse | Agent | `tokencast-agent-hook.sh` | Write span stop to sidecar JSONL |

Key patterns established in these hooks:
- `set -euo pipefail` + `|| exit 0` on every fallible command — fail-silent
- `SCRIPT_DIR`/`SKILL_DIR`/`CALIBRATION_DIR` path derivation pattern
- `python3 -c "..."` with data passed via environment variables (not shell interpolation) for injection safety
- Sampling gate (file size delta check) in midcheck.sh to avoid per-tool-call overhead
- `TOKENCOSTSCOPE_ESTIMATE_FILE` and related env-var overrides for testability
- All hooks reside in `scripts/`, invoked via absolute path in `settings.json`

The `calibration/` directory is gitignored and ephemeral. State files written there during sessions include `active-estimate.json`, `.midcheck-state`, `{session_id}-timeline.jsonl`, and `{session_id}-span-counter`.

### Hook Configuration Structure

`settings.json` is checked in and shared. `settings.local.json` is gitignored and accumulates session-specific permission grants. The enforcement hooks would need to live in `settings.json` to be active by default.

---

## Monarch Dashboard Hook Analysis

The monarch-dashboard project has 6 hooks that form the reference implementation for the enforcement requirements. Each is analyzed below.

### Hook 1: validate-agent-type.sh

**Location:** `.claude/hooks/validate-agent-type.sh`
**Event:** PreToolUse, matcher: Agent
**What it does:** Whitelists the `subagent_type` field of Agent tool calls against a hardcoded list of custom agent names. Any agent name not in the list triggers a hard block.
**Mechanism:** Reads `tool_input.subagent_type` from stdin JSON via `jq`. Loops over allowed list. Exits 2 if not found.
**Warn vs block:** Hard block — `exit 2`.
**Portability:** The agent list is project-specific but largely identical to tokencast's agents. Would need minor adjustments (remove monarch-specific agents like `commit-drafter`, `packet-summarizer`, `changelog-scanner`, etc.). The core pattern ports directly.

**Addresses:** Partially addresses R2 (prevents unknown agents, does not detect inline editing). Also enforces model assignment discipline.

### Hook 2: pre-push-gate.sh

**Location:** `.claude/hooks/pre-push-gate.sh`
**Event:** PreToolUse, matcher: Bash
**What it does:** Intercepts `git push` commands. Blocks the push unless a marker file exists at `$TMPDIR/claude-push-reviewed-$PPID`. The marker file is touched externally (by the operator confirming review is complete), then consumed on the next push attempt.
**Mechanism:** Extracts the Bash command from stdin JSON. Strips `$(...)` subexpressions (to avoid false positives from commit messages containing "git push"). Strips `-m "..."` inline commit message args. Checks for `git push` in the cleaned string. If found, checks for marker file. If no marker, outputs multi-line instruction message and exits 2.
**Warn vs block:** Hard block — `exit 2`.
**Portability:** Ports directly. The marker file path (`$TMPDIR/claude-push-reviewed-$PPID`) is self-contained. No project-specific logic.

**Addresses:** R6 partially (blocks push, not commit). R7 partially (blocks push until review gate is cleared). Does not check the current branch or distinguish `git commit` from `git push`.

**Gap noted:** This blocks push but not `git commit`. A direct `git commit` on main can still happen. The PM requirements (R6) ask for blocking commits on main specifically.

### Hook 3: edit-count-detector.sh

**Location:** `.claude/hooks/edit-count-detector.sh`
**Event:** PostToolUse, matcher: Edit|Write
**What it does:** Tracks how many times each file has been edited in the session using a per-PPID counter directory in TMPDIR. When a single file exceeds 5 edits, injects a warning into context.
**Mechanism:** Extracts `file_path` from `tool_input` via Python. Sanitizes path to a safe filename. Reads/increments a counter file. Outputs warning text when count >= threshold.
**Warn vs block:** Advisory warn — outputs plain text, no exit 2.
**Portability:** Ports directly. The session directory uses `$PPID` for isolation. Threshold (5) could be made configurable but is reasonable as-is.

**Addresses:** Loop detection — catches repetitive debugging cycles. Not directly mapped to any PM requirement, but helps with general quality. Does not address any of R1–R7 specifically.

### Hook 4: inline-edit-guard.sh

**Location:** `.claude/hooks/inline-edit-guard.sh`
**Event:** PostToolUse, matcher: Edit|Write
**What it does:** Tracks unique file paths edited since the last user message. When 3+ unique files are edited directly, warns that the work scope is S/M and should be delegated. The unique file list resets on each UserPromptSubmit via pipeline-gate.sh.
**Mechanism:** Maintains a `unique_files.txt` file in a per-PPID session directory. Adds file paths via `grep -qxF` + append (dedup). Counts lines. Outputs warning at threshold >= 3. The reset (delete `unique_files.txt`) happens in pipeline-gate.sh's UserPromptSubmit handler, not here.
**Warn vs block:** Advisory warn — outputs plain text, no exit 2.
**Portability:** Ports directly. The coordination with pipeline-gate.sh (reset on user message) must be preserved — both hooks share the `$TMPDIR/claude-unique-files-$PPID/unique_files.txt` path convention.

**Addresses:** R2 (inline execution detection) — this is the practical implementation. It does not distinguish orchestrator from sub-agent (see OQ-1), but acts as a proxy: if 3+ files are being edited directly without agent dispatch, something is wrong.

**Important finding on OQ-1:** The `agent_type` field in the hook input schema is only present when the hook fires inside a subagent (i.e., when Claude Code dispatched the sub-agent with `--agent` or via the Agent tool). When the orchestrator runs directly (top-level session), `agent_type` is absent. This means hooks CAN partially distinguish orchestrator from sub-agent context: if `agent_type` is present in the hook input, the call originated inside a dispatched agent. This makes R2 feasible as a hook — the inline-edit-guard can check for `agent_type` and suppress the warning when editing occurs inside a dispatched agent.

### Hook 5: pre-compact-reminder.sh

**Location:** `.claude/hooks/pre-compact-reminder.sh`
**Event:** PreCompact, no matcher
**What it does:** Outputs a multi-line reminder to stdout before context compaction. The reminder restates agent delegation rules and instructs the orchestrator to re-read the compaction summary before resuming.
**Mechanism:** Simple `cat <<'MSG'` heredoc. No stdin processing, no state files.
**Warn vs block:** Advisory — outputs plain text. PreCompact cannot block compaction (it's observation-only per the hook API).
**Portability:** Ports directly with minor text changes for tokencast context. The tokencast CLAUDE.md already mentions compaction concerns (see OQ-3 in requirements).

**Addresses:** OQ-3 (pipeline state surviving compaction). This hook fires right before compaction — a good moment to remind Claude to check `pipeline-state.json` when it resumes.

### Hook 6: pipeline-gate.sh

**Location:** `.claude/hooks/pipeline-gate.sh`
**Event:** UserPromptSubmit, no matcher
**What it does:** Two responsibilities: (1) resets the inline-edit-guard's unique file counter on each new user message; (2) injects a pipeline classification reminder into Claude's context for any prompt longer than 20 characters.
**Mechanism:** Reads prompt from stdin JSON via `jq`. Checks prompt length. Deletes `$TMPDIR/claude-unique-files-$PPID/unique_files.txt`. Outputs a formatted pipeline gate reminder with a size classification table.
**Warn vs block:** Advisory — outputs plain text. Does not exit 2.
**Portability:** Ports directly. The prompt length gate (>20 chars) is a reasonable heuristic. The size classification table matches tokencast's CLAUDE.md exactly.

**Addresses:** R3 upstream (classification reminder). Helps with OQ-3 (context compaction) by re-injecting instructions on every user message.

---

## Claude Code Hook API — Definitive Reference

### Available Hook Events

From the official documentation (code.claude.com/docs/en/hooks):

| Event | Fires When | Can Hard-Block? | Data Available |
|-------|-----------|----------------|----------------|
| PreToolUse | Before any tool call | YES (exit 2 or `permissionDecision: "deny"`) | tool_name, tool_input, session_id, transcript_path, agent_type (if in subagent) |
| PostToolUse | After tool call succeeds | NO (tool already ran) | tool_name, tool_input, tool_output |
| UserPromptSubmit | Before Claude processes user message | YES (exit 2 or `decision: "block"`) | prompt |
| SessionStart | Session begins/resumes/compacts | NO (advisory) | source (startup/resume/clear/compact), model |
| PreCompact | Before context compaction | NO (advisory) | (no extra fields documented) |
| Stop | When Claude finishes responding | YES (prevents stopping, forces continuation) | stop_hook_active, last_assistant_message |
| SubagentStart | When a sub-agent spawns | NO (advisory) | agent_type, agent_id |
| SubagentStop | When a sub-agent finishes | YES (prevents stopping) | agent_type, agent_id, stop_hook_active |

Additional events exist (TaskCreated, TaskCompleted, TeammateIdle, FileChanged, etc.) but are less relevant here.

### Exit Code Semantics

- Exit 0: Allow. Stdout parsed for JSON if present.
- Exit 2: Block the event (for events that support blocking). Stderr fed back to Claude as error context. Stdout ignored.
- Any other exit code: Non-blocking error. Stderr shown in verbose mode.

### JSON Output Format

PreToolUse blocking/advisory:
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Message shown to user",
    "additionalContext": "Message added to Claude's context"
  }
}
```

PreToolUse with exit 2 is simpler and well-established: write message to stderr, exit 2.

PostToolUse advisory (exit 0 with additionalContext):
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "Message Claude sees"
  }
}
```

UserPromptSubmit: plain stdout text is added to Claude's context directly. Or JSON with `decision: "block"`.

### Critical Finding: Hard-Block vs Advisory

The PM requirements document states (C-4): "Hooks cannot hard-block tool execution — they output context that Claude reads." This is **partially incorrect** as of current Claude Code.

PreToolUse hooks CAN hard-block tool calls via exit 2. The validate-agent-type.sh and pre-push-gate.sh hooks demonstrate this in production. The monarch hooks have been running in production and `exit 2` does prevent the tool from executing.

The PM's OQ-2 ("what makes enforcement messages different from advisory?") has a concrete answer: `exit 2` from PreToolUse completely prevents the tool call from firing. Claude does not proceed. This is a genuine enforcement mechanism, not just advisory.

**For hooks that fire on PostToolUse (like inline-edit-guard), blocking is not possible** — the action already happened. These are advisory only.

### Agent Type Field (Resolves OQ-1)

The hook input envelope includes `agent_type` when the hook fires inside a dispatched sub-agent. When the orchestrator is running at the top level (no `--agent` flag), `agent_type` is absent. This provides a workable (though imperfect) signal for R2:

- `agent_type` present and non-empty: hook is firing inside a sub-agent. Inline edits are expected behavior.
- `agent_type` absent: hook is firing in the orchestrator context. Inline edits should trigger warnings.

This resolves OQ-1 as "feasible with a caveat": the distinction is available, but only for hooks that receive the full common envelope. The inline-edit-guard (PostToolUse) should receive this field.

---

## Global vs Local Hook Placement

### What Belongs in ~/.claude/settings.json (Global, All Projects)

- The pipeline gate reminder (UserPromptSubmit) is project-agnostic and useful everywhere.
- The pre-compact reminder is project-agnostic.
- The validate-agent-type hook is somewhat project-agnostic (the allowed list is the same across projects using the same agent set).
- The inline-edit-guard and edit-count-detector are project-agnostic.

However: monarch-dashboard keeps all its enforcement hooks in `.claude/hooks/` with the settings in `.claude/settings.json` (project-local). This is the safer default — global hooks affect all projects, including ones where the rules don't apply.

### What Belongs in .claude/settings.json (Project-Local, Tokencast)

- All tokencast-specific hooks (midcheck, learn, agent-hook, track) are already project-local.
- The enforcement hooks should be project-local for the same reason: the calibration directory path, the `pipeline-state.json` location, and the tokencast-specific rules (check `active-estimate.json`) are project-specific.
- The `TOKENCAST_SKIP_GATE=1` escape hatch is project-scoped.

### Recommendation on Global vs Local

Keep all enforcement hooks in the project's `.claude/settings.json` for tokencast. If the owner wants the pipeline classification reminder and delegation guard globally (across all projects), extract those two to `~/.claude/settings.json` and point them at scripts in `~/.claude/hooks/`. The tokencast-specific gates (active-estimate.json check, pipeline-state.json check) remain project-local.

---

## Cost Estimate Enforcement Options (R1)

### How to Detect That /tokencast Has Been Run

The tokencast skill writes `calibration/active-estimate.json` when it runs. This file's mtime is the signal. Three sub-questions:

**Q: Is the estimate "current" for this session?**
The midcheck.sh approach uses `-newer "$ESTIMATE_FILE"` to find JSONL files written after the estimate. The same logic applies here: `active-estimate.json` mtime should be newer than the session JSONL start. In practice, a simpler check works: does the file exist AND was it written within the last N hours (e.g., 24h)? This is the same recency check used in `parse_last_estimate.py`.

**Q: Does the check need to happen on every Agent tool call?**
No. Only when the agent being dispatched is an implementation-phase agent (`implementer`, `qa`, `debugger`). The hook can read `tool_input.name` (or `tool_input.subagent_type`) to filter.

**Q: What about multi-story sessions?**
The PM requirement acknowledges this: each story needs a fresh estimate. A simple heuristic: if `active-estimate.json` exists but its mtime is older than N hours (suggesting it's from a prior story), the gate fires. The pipeline-state.json (R3) would provide a more precise signal once implemented.

### Option A: PreToolUse on Agent Tool, Check File Existence

Check `calibration/active-estimate.json` exists and is recent. If not, output message to stderr and exit 2.

- Pro: Hard block. Implementation-phase dispatch truly prevented.
- Pro: Simple — single file existence check, fast.
- Con: `mtime` recency is imprecise for multi-story sessions.
- Con: Only blocks Agent tool dispatch; orchestrator can still write implementation files inline (R2 catches that separately).
- Effort: Low.

### Option B: PreToolUse on Agent Tool, Check pipeline-state.json

Check `pipeline-state.json` for `PP-7` completion. If not present, block.

- Pro: More precise than file mtime.
- Pro: Unified gate for R1, R3, R4 (single state file check).
- Con: Requires pipeline-state.json to exist (i.e., orchestrator must create it). If orchestrator skips that step, no gate fires. Circular dependency.
- Con: More complex — requires the state file system to work correctly.
- Effort: Medium (depends on R3 implementation).

### Option C: PostToolUse Advisory Only (Current Pattern)

Strengthen the `tokencast-track.sh` message and change behavior to also output a reminder at every Agent dispatch when no estimate exists.

- Pro: No changes to PreToolUse, cannot break the session.
- Pro: Already implemented — just strengthen the message.
- Con: Advisory. The Phase 1 session proved advisory messages are ignored at scale.
- Effort: Minimal.

**Recommendation for R1:** Option A as the primary gate. It requires no new infrastructure (active-estimate.json already exists), is fast (single stat call), and provides a hard block. Add Option B integration later once pipeline-state.json is stable.

---

## Pipeline State Tracking Options (R3)

### Option A: Orchestrator-Written State File

Orchestrator writes `calibration/pipeline-state.json` at the start of each story and updates it as steps complete. The PreToolUse gate checks the file.

- Pro: Simple schema. Orchestrator has full control.
- Pro: File persists through compaction (on disk). PreCompact hook can remind orchestrator to re-read it.
- Con: Relies on orchestrator to write and maintain the file — the same agent that violated the pipeline. If it skips writing, there is no gate.
- Con: OQ-3: after compaction, will the orchestrator remember to check the file? The pipeline-gate.sh UserPromptSubmit hook can inject the file's contents to address this.
- Effort: Medium.

### Option B: PostToolUse Hook Auto-Updates State File

A PostToolUse hook on the Agent tool maps agent names to pipeline steps and auto-writes `pipeline-state.json` entries. No orchestrator action required — completing an agent dispatch automatically records the step.

- Pro: Does not rely on orchestrator compliance to record steps.
- Pro: Works even if orchestrator forgets to update the state file manually.
- Con: The hook cannot know the `story_id` or `size` without the orchestrator writing them first.
- Con: Agent name → pipeline step mapping is fragile (same agent used for multiple steps, e.g., `engineer` for PP-4 and PP-6).
- Effort: Medium-high.

### Option C: Session Context Injection Only (No State File)

Use UserPromptSubmit (pipeline-gate.sh) to inject the pipeline checklist reminder on every user message. No state file. Rely on Claude's attention to the injected context.

- Pro: No new infrastructure.
- Pro: Addresses OQ-3 by re-injecting the reminder at every turn.
- Con: No hard gate — still advisory.
- Con: Does not help with the enforcement requirement (R3 wants blocking).
- Effort: Low (already partially implemented in monarch).

**Recommendation for R3:** Option A is the right architecture. The state file must be writable by the orchestrator (the only agent that knows the story_id and size) and readable by the gate hook. Mitigate the "orchestrator forgets to create it" risk by: (1) the UserPromptSubmit hook injecting a reminder to create it when it doesn't exist and the task appears to be M/L size; (2) the PreToolUse gate failing open (not blocking) when the state file doesn't exist, to avoid hard-blocking XS/S work.

---

## Gaps: What Monarch Hooks Don't Cover

### Gap 1: Active-Estimate Gate (R1)

Monarch has no tokencast integration. The estimate gate (check `active-estimate.json` before `implementer` dispatch) is new. There is no reference implementation to port — it must be built.

### Gap 2: Pipeline State File Write/Read (R3)

Monarch has no pipeline-state.json concept. The UserPromptSubmit hook (pipeline-gate.sh) injects a reminder, but there is no persistent state tracking per story. The full R3 implementation (state file schema, write/update logic, PreToolUse gate reading the file) is new.

### Gap 3: TDD Ordering Gate (R4)

Monarch does not track whether `qa` was dispatched before `implementer`. The `tests_written` boolean gate is new. It requires either the PostToolUse hook to record `qa` dispatch, or the orchestrator to write it to the state file.

### Gap 4: Memory/Docs Update Gate (R5)

Monarch has no docs-update tracking. The `docs_updated` gate at merge time is new.

### Gap 5: PR Review Loop State (R7)

Monarch's pre-push-gate.sh blocks pushes behind a marker file, but it has no concept of review loop passes or the "zero findings = clean" exit condition. The orchestrator sets the marker file, making it as circumventable as an advisory message. A stricter version would record staff-reviewer dispatch and findings count.

### Gap 6: Commit-to-Main Block (R6, partial)

Monarch's pre-push-gate.sh blocks `git push` but not `git commit`. Direct commits to main (as in the Phase 1 violation) would pass through. A separate Bash PreToolUse check for `git commit` on `main` branch is needed.

### Gap 7: Branch State Check in pre-push-gate

The monarch pre-push-gate.sh does not verify that the current branch is not main. A PR might be pushed from main directly. A branch check would close this.

### Gap 8: TOKENCAST_SKIP_GATE Environment Variable

No monarch hook checks for an escape hatch env var. This is a new requirement (C-1) — all tokencast enforcement hooks must check `TOKENCAST_SKIP_GATE=1` and exit 0 if set.

---

## Options Evaluated

### Option 1: Port Monarch Hooks + Add New Enforcement Scripts (Recommended)

Port all 6 monarch hooks with adaptation, add 2 new scripts for the tokencast-specific gates (estimate gate + branch check), and use pipeline-state.json for state tracking.

**Scripts:**
- Port `validate-agent-type.sh` → adapt allowed list for tokencast agents
- Port `pre-push-gate.sh` → add branch check and SKIP_GATE support
- Port `edit-count-detector.sh` → direct copy
- Port `inline-edit-guard.sh` → add agent_type check to suppress warnings in sub-agents
- Port `pre-compact-reminder.sh` → adapt text for tokencast
- Port `pipeline-gate.sh` → add `pipeline-state.json` injection when file exists
- New: `pipeline-enforcement-gate.sh` → PreToolUse on Agent: check active-estimate.json + pipeline-state.json
- New: `commit-branch-guard.sh` → PreToolUse on Bash: check git commit on main branch

**Settings.json changes:**
Add PreToolUse matchers for Agent (enforcement gate) and Bash (branch + push guard), PostToolUse matchers for Edit/Write (loop detector + delegation guard), PreCompact (reminder), UserPromptSubmit (pipeline gate).

- Pros: Reuses proven patterns from monarch. All hooks follow established fail-silent conventions. Incremental — can deploy in phases.
- Cons: 8 scripts total; more entries in settings.json; more latency per tool call (though each is fast).
- Effort: Medium. Porting is low effort; the new enforcement gate is medium.
- Compatibility: High — follows existing tokencast hook conventions exactly.

### Option 2: Single Monolithic Gate Script

One script (`pipeline-gate-all.sh`) handles all enforcement logic: estimate check, pipeline state check, branch check, delegation detection.

- Pros: One hook entry per event type. Simpler settings.json.
- Cons: Violates single-responsibility. A bug in one gate breaks all enforcement. Harder to test. Harder to extend. If the script crashes, all gates fail open simultaneously.
- Effort: Medium — same total logic, worse maintainability.
- Compatibility: Medium — departs from the monarch per-concern pattern.

### Option 3: CLAUDE.md Rule Strengthening Only (No New Hooks)

Rewrite CLAUDE.md with stronger, more specific language. Add the consequence framing the PM requirements specify. No new hooks.

- Pros: Minimal infrastructure. Zero latency overhead. No hook maintenance.
- Cons: All existing advisory messages were already ignored at scale. The PM requirements document specifically identifies this as insufficient (RC-1: "rules in CLAUDE.md are advisory text... nothing prevented it from skipping steps"). The Phase 1 session proved this does not work for 19-story batch sessions.
- Effort: Low.
- Compatibility: N/A — does not use the hook system at all.

### Option 4: Phased Deployment (Subset First)

Deploy only the highest-leverage hooks first (estimate gate R1, branch guard R6, pipeline classification reminder R3-advisory), then add the full pipeline-state.json tracking in a follow-up.

- Pros: Lower initial complexity. Addresses the highest-severity violations (V1 100%, V6) with minimal infrastructure. Can validate hook compliance rate before investing in full state tracking.
- Cons: Leaves R3, R4, R5, R7 advisory-only initially.
- Effort: Low-medium for phase 1; medium for phase 2.
- Compatibility: High.

---

## Recommendation

**Option 1 (port all + add new) implemented as Option 4 (phased).**

Phase 1 (highest ROI, low risk):
1. Port `pipeline-gate.sh` (UserPromptSubmit classification reminder)
2. Port `pre-compact-reminder.sh` (PreCompact reminder, with tokencast context)
3. Port `validate-agent-type.sh` (Agent whitelist)
4. New `pipeline-enforcement-gate.sh` (PreToolUse Agent: estimate gate R1)
5. New `commit-branch-guard.sh` (PreToolUse Bash: main branch commit block R6)
6. Port `pre-push-gate.sh` with branch check added (R6/R7)

Phase 2 (after phase 1 validated):
7. Port `inline-edit-guard.sh` with agent_type suppression (R2)
8. Port `edit-count-detector.sh` (loop detection)
9. Implement pipeline-state.json schema and gate (R3, R4, R5, R7)

Rationale: Phase 1 covers the two 100%-frequency violations (V1: no estimate, V6: commits to main) plus the classification reminder that addresses V3, V4, V5 at the advisory level. It requires no new state file infrastructure. Phase 2 adds the full state tracking after the simpler hooks have proven themselves.

**On OQ-2 (can hooks effectively block):** Yes. PreToolUse with exit 2 is a genuine hard block, not just advisory. This changes the architecture from "strongly worded advisory" to "actual enforcement." The requirement document's assumption (C-4) that hooks cannot hard-block is outdated; the actual Claude Code API supports it.

**On OQ-6 (tokencast feature vs separate concern):** Keep it in tokencast. The infrastructure (calibration directory, settings.json, hook patterns) is already there. The only tokencast-specific gate is R1; R2, R3, R6 are workflow-general and could theoretically be global, but the simplicity of keeping everything in one place outweighs the conceptual separation benefit.

---

## Open Questions

### OQ-A: Settings.json Hook Ordering and Latency

With 5–8 hooks in settings.json, what is the actual latency per tool call? The midcheck.sh hook already fires on every PreToolUse call with a 50KB sampling gate. Adding 1–2 more PreToolUse hooks that do only a single `stat` call (to check file mtime) should add <10ms. Needs measurement before claiming compliance with C-2 (500ms budget).

### OQ-B: PPID Stability Across Sub-Agent Contexts

Monarch's hooks use `$PPID` to namespace session state (counter dirs, unique file lists). In tokencast's existing hooks, session state uses `session_id` from the hook envelope. When sub-agents run, do they share the same `$PPID` as the orchestrator? If not, the inline-edit-guard (which uses `$PPID` for its counter dir) would not correctly accumulate edits across the orchestrator and sub-agents into the same counter. This needs verification before porting inline-edit-guard.sh.

### OQ-C: Does exit 2 from PreToolUse produce visible feedback?

The monarch pre-push-gate.sh and validate-agent-type.sh both use exit 2 with stderr output. The documentation says "stderr is fed back as error message to Claude." Does this appear in Claude's context reliably, or does it require a specific output format? Testing against a real tool call is needed.

### OQ-D: `agent_type` in PostToolUse vs PreToolUse

The inline-edit-guard needs to detect orchestrator vs sub-agent context. The `agent_type` field is documented as present in common hook input when inside a sub-agent. But PostToolUse hooks receive a different input schema (tool_name, tool_input, tool_output) plus the common fields. Confirm that `agent_type` is included in the PostToolUse envelope when the edit happens inside a sub-agent.

### OQ-E: pipeline-state.json and the Orchestrator Trust Problem

The PM requirements note that the orchestrator that violated the pipeline is the same agent that would write the state file. If the orchestrator skips creating `pipeline-state.json`, the gate has nothing to check. Options: (1) fail-open when state file is absent (XS/S work proceeds unblocked — acceptable); (2) the UserPromptSubmit hook creates a minimal state file if absent; (3) accept the limitation and treat the state file as an honor system with enforcement only when the file exists. This is an architecture decision.

---

## Relevant File Paths

Reference implementation (monarch):
- `/Users/kellyl./Documents/Cowork Projects/Personal Finance/monarch-dashboard/.claude/hooks/validate-agent-type.sh`
- `/Users/kellyl./Documents/Cowork Projects/Personal Finance/monarch-dashboard/.claude/hooks/pre-push-gate.sh`
- `/Users/kellyl./Documents/Cowork Projects/Personal Finance/monarch-dashboard/.claude/hooks/edit-count-detector.sh`
- `/Users/kellyl./Documents/Cowork Projects/Personal Finance/monarch-dashboard/.claude/hooks/inline-edit-guard.sh`
- `/Users/kellyl./Documents/Cowork Projects/Personal Finance/monarch-dashboard/.claude/hooks/pre-compact-reminder.sh`
- `/Users/kellyl./Documents/Cowork Projects/Personal Finance/monarch-dashboard/.claude/hooks/pipeline-gate.sh`
- `/Users/kellyl./Documents/Cowork Projects/Personal Finance/monarch-dashboard/.claude/settings.json`

Tokencast existing hooks:
- `/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-midcheck.sh`
- `/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-agent-hook.sh`
- `/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-track.sh`
- `/Volumes/Macintosh HD2/Cowork/Projects/costscope/scripts/tokencast-learn.sh`
- `/Volumes/Macintosh HD2/Cowork/Projects/costscope/.claude/settings.json`

Requirements:
- `/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/plans/pipeline-enforcement-requirements.md`
