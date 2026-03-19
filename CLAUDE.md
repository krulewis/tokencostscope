# tokencostscope

A Claude Code skill that automatically estimates Anthropic API token costs when a development plan is created, and learns from actual usage over time to improve accuracy via calibration factors.

## Repo

- GitHub: `krulewis/tokencostscope`
- Current version: 1.3.1

## Key Files

| Path | Purpose |
|------|---------|
| `SKILL.md` | Skill definition — activation rules, calculation algorithm, output template |
| `references/heuristics.md` | Token budgets, pipeline step decompositions, complexity multipliers, parallel discount parameters — all tunable parameters live here |
| `references/pricing.md` | Model pricing per million tokens, cache rates, step→model mapping |
| `references/calibration-algorithm.md` | Calibration algorithm documentation |
| `references/examples.md` | Worked estimation examples |
| `scripts/tokencostscope-learn.sh` | Stop hook — reads session JSONL at end of session, computes actuals, calls update-factors.py |
| `scripts/update-factors.py` | Computes and persists calibration factors from completed session data |
| `scripts/sum-session-tokens.py` | Parses session JSONL to sum token costs |
| `calibration/` | Calibration data directory — gitignored; contains `active-estimate.json` and `factors.json` |
| `tests/test_pr_review_loop.py` | Tests for PR Review Loop cost modeling |
| `tests/test_parallel_agent_accounting.py` | Tests for parallel agent cost discounting |
| `docs/wiki/` | GitHub wiki source — Home, How-It-Works, Installation, Configuration, Calibration, Roadmap |
| `README.md` | Repo root README (not inside `.claude/skills/tokencostscope/`) |

## Test Commands

```bash
# Run all tests — use system Python 3.9 which has pytest
/usr/bin/python3 -m pytest tests/

# Run a specific test file
/usr/bin/python3 -m pytest tests/test_pr_review_loop.py

# Run with verbose output
/usr/bin/python3 -m pytest tests/ -v
```

**Do NOT use `pytest` or `python3 -m pytest` directly.** Homebrew `python3` resolves to 3.14 which does NOT have pytest. Always use `/usr/bin/python3`.

## Architecture Conventions

- **All tunable parameters live in `references/heuristics.md`** — not hardcoded in SKILL.md. This includes complexity multipliers, band multipliers, parallel discount factors, cache rate floors, and review cycle defaults.
- **Shell injection safety** — `learn.sh` uses `shlex.quote()` and env vars pattern to pass data to Python. Never interpolate user-derived strings directly into shell commands.
- **`active-estimate.json` is the handshake** between estimation (SKILL.md writes it at estimate time) and learning (learn.sh reads it at session end). Schema changes must be backward compatible.
- **Backward compatibility** — new fields in `active-estimate.json` and `factors.json` schemas use `.get()` defaults in Python so old files don't break newer scripts.
- **Version string must be consistent** across three places: `SKILL.md` frontmatter (`version:`), output template header (`## costscope estimate (v1.x.x)`), and `learn.sh` `VERSION` variable. Always update all three together.
- **PR Review Loop calibration** applies the factor independently to each band (not re-anchored as fixed ratios of calibrated Expected) — this preserves the decay model's per-band cycle counts.
- **Step 3.5 runs post-step-loop** — the PR Review Loop row computation happens after all individual pipeline steps complete Steps 3a–3e, not inline. Cache each constituent step's pre-discount cost during the per-step loop.
- **Parallel discount does NOT apply to PR Review Loop C value** — `C` uses undiscounted step costs even when constituent steps were modeled as parallel.

## Memory / Docs Update Paths

When completing work, the `docs-updater` agent should update:
- `docs/wiki/` — whichever wiki pages cover the changed functionality
- `MEMORY.md` at `/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md`
- `ROADMAP.md` if version or milestone status changed

## Gotchas

- **Paths with spaces** — always quote shell paths; use `-print0 | xargs -0` for `find` pipelines. The repo lives at `/Volumes/Macintosh HD2/Cowork/Projects/costscope` — the space in "Macintosh HD2" will break unquoted shell commands.
- **macOS volume path** — `/Volumes/Macintosh HD2/...` is the working directory; scripts run from there will have the space in the absolute path.
- **Worktree working directory** — if using git worktrees, the working dir differs from the main repo root. Use absolute paths.
- **README.md location** — `README.md` is in the repo root (`/Volumes/Macintosh HD2/Cowork/Projects/costscope/README.md`), not inside `.claude/skills/tokencostscope/`.
- **`calibration/` is gitignored** — do not commit calibration data. The directory may not exist on a fresh clone; scripts must handle its absence gracefully.

---

<!-- Pipeline imported from ~/.claude/CLAUDE.md -->

# Global Development Rules — Kelly Lewis

These rules apply to **all projects**. Project-level CLAUDE.md files add project-specific paths and conventions on top of these.

**Decision rationale:** `~/.claude/docs/workflow-decisions.md` — why each step, model assignment, and gate exists.

---

## Planning Pipeline

### Change Size Classification

| Size | Description | Pipeline |
|------|-------------|----------|
| **XS** | Single file, trivial fix, < 5 lines, no tests affected | May bypass |
| **S** | 1–2 files, clear scope, no architectural decisions | Optional |
| **M** | Multi-file, new feature, involves tests, clear scope | **Required** |
| **L** | New systems, architectural decisions, cross-cutting concerns | **Required** |

### Pipeline Steps (M and L — run each as a separate agent with fresh context)

1. **PM Agent** — dispatch to `pm` agent → requirements document
2. **Research Agent** — dispatch to `researcher` agent → written report
3. **Architect Agent** — dispatch to `architect` agent → architecture decision with rationale and rejected alternatives
3b. **Frontend Designer** (UI features only) — dispatch to `frontend-designer` agent → design specification with component designs, tokens, states, responsive behavior
4. **Engineer Agent — Initial Plan** — dispatch to `engineer` agent → file-level implementation plan with parallelism tags (incorporates design spec for UI work)
5. **Staff Engineer Agent — Review** — dispatch to `staff-reviewer` agent → pressure-tests plan for bugs, ambiguities, edge cases, incorrect assumptions → required changes list
6. **Engineer Agent — Final Plan** — dispatch to `engineer` agent (with staff feedback as input) → corrected plan ready for implementation
7. **Cost Estimate** — run `/tokencostscope` on the final plan → token/dollar estimate for remaining steps (implementation, QA, review loop). Record estimate before proceeding.

**Never skip or combine steps.** Fresh-context agents catch what prior agents missed.

### Parallelism

Run pipeline steps and build processes as **parallel agents** whenever their inputs are independent. Only serialize when a step requires output from a prior step (e.g., Architect depends on Research) or when changes touch the same files/branch (rebase conflicts).

**Pipeline parallelism:**
- Research Agent + PM clarification follow-ups can overlap
- Multiple independent searches, reads, or validations should always be parallel

**Implementation parallelism:**
- The `engineer` agent's plan tags each change as `independent` or `depends-on: <other change>`
- When a plan identifies independent file groups, spawn multiple `implementer` agents in parallel — one per independent group
- `qa` agent can begin writing tests in parallel with implementation when test interfaces are defined in the plan
- `docs-updater` runs in parallel with QA after implementation completes
- PR review loop fixes on independent files can be parallelized across `implementer` or `debugger` agents

When in doubt, prefer parallel — the cost of a wasted agent is lower than the cost of idle waiting.

### Agent Teams

For M/L changes, use **TeamCreate** to coordinate agents via shared task lists instead of ad-hoc sequential dispatch. Teams formalize parallelism and make agent coordination explicit.

**When to use teams:** Any M/L change where 3+ agents will run, or when parallel agent coordination is needed.

**Team lifecycle:**
1. `TeamCreate` — creates the team and its task list
2. `TaskCreate` — break the work into discrete tasks
3. Spawn teammates via Agent tool with `team_name` and `name` parameters
4. `TaskUpdate` — assign tasks to teammates, track progress
5. Teammates work, complete tasks, and go idle between turns
6. `SendMessage` with `type: "shutdown_request"` — gracefully shut down teammates when done
7. `TeamDelete` — clean up team resources after all teammates shut down

**Standard team compositions:**

| Phase | Team Name | Members | Notes |
|-------|-----------|---------|-------|
| **Planning** | `{feature}-planning` | `pm`, `researcher` | PM interviews user; researcher explores codebase/web in parallel. Architect + engineer run after (sequential dependency). |
| **Implementation** | `{feature}-impl` | `qa`, `implementer` (x N), `frontend-designer`, `docs-updater` | QA writes tests first. Implementers work independent file groups in parallel. Frontend-designer provides design specs for UI work. Docs-updater runs alongside or after implementation. |
| **Review** | `{feature}-review` | `staff-reviewer`, `implementer` / `debugger` | Staff reviewer finds issues → implementer/debugger fix → fresh staff-reviewer pass. |

**Team rules:**
- Each team phase corresponds to a workflow stage — don't mix planning and implementation agents in one team
- Spawn a **new team** for each phase (planning → implementation → review) to keep context clean
- Tasks must have clear ownership — never leave a task unassigned when agents are idle
- The orchestrator monitors the task list and reassigns/unblocks as needed
- Delete each team after its phase completes before creating the next one

**When NOT to use teams:**
- XS/S changes (overhead exceeds benefit)
- Single-agent tasks (just dispatch directly)
- Sequential-only work with no parallelism opportunity

### Model Selection Principle

Models are defined in each agent's frontmatter — not chosen at dispatch time. The tier philosophy behind assignments:

| Tier | Model | Criteria | Agents |
|------|-------|----------|--------|
| **Critical judgment** | opus | Mistakes are expensive and hard to reverse | `pm`, `architect`, `staff-reviewer` |
| **Standard work** | sonnet | Produces artifacts by following patterns | `researcher`, `engineer`, `implementer`, `qa`, `code-reviewer`, `debugger`, `frontend-designer` |
| **Mechanical** | haiku | Procedural tasks, no deep reasoning needed | `explorer`, `docs-updater`, `playwright-qa` |

### Agent Delegation — MANDATORY

**All execution work MUST be dispatched to a named agent.** The orchestrator coordinates and dispatches but does not perform execution tasks inline.

**Agent → Pipeline Step Mapping:**

| Pipeline Step | Agent |
|---------------|-------|
| 1. Requirements interview | `pm` |
| 2. Research | `researcher` |
| 3. Architecture | `architect` |
| 3b. UI design (UI features) | `frontend-designer` |
| 4. Initial plan | `engineer` |
| 5. Plan review | `staff-reviewer` |
| 6. Final plan | `engineer` |
| 7. Cost estimate | `/tokencostscope` (inline) |
| 3. Write tests | `qa` |
| 4. Implement | `implementer` |
| 5. Update docs | `docs-updater` |
| 7. UI QA | `playwright-qa` |
| 9. PR review | `staff-reviewer` |
| 9. PR fixes | `implementer` / `debugger` |
| 10. Cost analysis | `/tokencostscope` (inline) |
| Ad-hoc search | `explorer` |
| Ad-hoc review | `code-reviewer` |
| UI/UX design | `frontend-designer` |

**Exception:** XS/S changes where the total work is < 5 tool calls — the orchestrator may execute inline rather than spawning an agent.

---

## Development Workflow (strict order — do not skip or reorder)

1. **Planning pipeline** (required for M/L) — use a `{feature}-planning` team. Dispatch to `pm`, `researcher` (can overlap), then `architect`, `engineer`, `staff-reviewer` agents per pipeline steps above. For UI features, include `frontend-designer` after architecture to produce design specs before engineering plan.
2. **Confirm** approach with user before writing code — this is the **only** user check-in in the pipeline. All subsequent steps run autonomously without waiting for user input.
3. **Write tests first** — dispatch to `qa` agent. Tests must fail before implementation exists. Cover happy path, edge cases, and error cases.
4. **Implement** — use a `{feature}-impl` team. Spawn `qa`, `implementer` (x N for independent file groups), `frontend-designer` (for UI work), and `docs-updater` as teammates. Coordinate via shared task list.
5. **Update memory and docs** — dispatch to `docs-updater` agent before QA (see project CLAUDE.md for paths)
6. **Run all automated tests** — failures → return to step 4
7. **Playwright UI QA** — dispatch to `playwright-qa` agent. Exercise the feature in the running app, take a screenshot — issues → return to step 4
8. **Commit to feature branch** — push and create PR against main via `gh pr create`
9. **PR Review Loop** — repeat until clean:
   i. Dispatch to `staff-reviewer` agent with **fresh context**. Only inputs: PR diff (`gh pr diff`) + project CLAUDE.md
   ii. Reviews for bugs, logic errors, edge cases, security, style → numbered findings list
   iii. Dispatch fixes to `implementer` or `debugger` agent as appropriate. Commit, push, re-run tests.
   iv. Dispatch to **new** `staff-reviewer` agent (fresh context) → repeat from (i)
   v. **Exit:** Staff Engineer states "no remaining comments"
   vi. **Loop guard:** same comment on two consecutive passes → stop and flag to user
10. **Cost Analysis** — run `/tokencostscope` actual-vs-estimate comparison. Before invoking, read `calibration/last-estimate.md` and `calibration/active-estimate.json` if they exist to recover the prior estimate (survives session compaction). Report the delta and update calibration data for future estimates.
11. **Merge** — merge automatically after the PR review loop is clean (no user confirmation needed).

### Checklists (post visibly in responses)

**PRE-WORK** (before writing code):
```
[ ] Change size classified — pipeline run if M/L
[ ] Cost estimate recorded (tokencostscope)
[ ] Plan confirmed with user
[ ] Tests written before implementation
```

**POST-WORK** (after completing):
```
[ ] Tests: written first (failed initially), all passing (new + existing)
[ ] Memory/docs updated before QA
[ ] Playwright QA — screenshot taken
[ ] Cost analysis — actual vs estimate compared, calibration updated
[ ] PR review loop clean — no comments on final pass
[ ] Merged to main automatically after clean PR review pass
```
