# Requirements: Documentation & Memory Restructuring

**Date:** 2026-03-27
**Size:** S (documentation only, no code changes)
**Status:** Ready for implementation

---

## 1. Clarified Intent

Restructure the tokencast project's documentation and memory files to eliminate duplication between CLAUDE.md and MEMORY.md, establish single sources of truth for architecture/conventions/gotchas as standalone reference docs, organize the growing docs/ directory with proper categorization, and create a plan index for the 18 existing plan files.

The target structure is modeled on the monarch-dashboard repo pattern where CLAUDE.md is a slim operational reference (key files, test commands, hooks, pointers) and deep knowledge lives in dedicated docs/ files.

---

## 2. User Stories & Acceptance Criteria

### US-DR-01: Create docs/architecture.md

**As** a new-session agent, **I want** a single architecture reference doc **so that** I don't need to piece together architecture from CLAUDE.md bullet points and MEMORY.md sections.

**Acceptance Criteria:**
- File exists at `docs/architecture.md`
- Contains all architecture content currently in CLAUDE.md lines 87-113 (the "Architecture Conventions" section)
- Contains all architecture content currently in MEMORY.md lines 84-110 ("Architecture Conventions" section) — deduplicated, not copied twice
- Content that exists in both files appears exactly once, with the more detailed version preserved
- Organized with clear H2 headings: Python Package Design, Session Recording & Calibration, Estimation Algorithm, Data Modules, Backward Compatibility, File Size Awareness, MCP Layer, Attribution Protocol
- No content is lost — every architecture bullet from both source files appears in the new doc

### US-DR-02: Create docs/conventions.md

**As** an implementer agent, **I want** coding conventions in one place **so that** I follow the right patterns without scanning CLAUDE.md and MEMORY.md.

**Acceptance Criteria:**
- File exists at `docs/conventions.md`
- Extracts coding patterns from CLAUDE.md and MEMORY.md that are conventions (not architecture): version string consistency rule, shell injection safety, `.get()` defaults for backward compat, hook placement rule (hooks/ vs scripts/), package exports requirement, lazy `__init__.py` pattern, CI portability rules (REPO_ROOT, sys.executable, error logging)
- Each convention has a one-line summary and then the detail
- No duplication with `docs/architecture.md` — draw a clear line (architecture = structural decisions; conventions = coding rules to follow)

### US-DR-03: Create docs/gotchas.md

**As** any agent working in the codebase, **I want** a consolidated gotchas reference **so that** I don't hit known pitfalls.

**Acceptance Criteria:**
- File exists at `docs/gotchas.md`
- Contains all gotchas from CLAUDE.md lines 126-148 ("Gotchas" section, 4 subsections)
- Contains all gotchas from MEMORY.md lines 138-166 ("Gotchas" section, 5 subsections)
- Deduplicated: items that appear in both files appear once (e.g., "Paths with spaces", "Python versions", "MCP requires >= 3.10")
- MEMORY.md-only gotchas preserved: macOS `timeout` command, midcheck.sh JSONL discovery, estimate_cost does NOT write active-estimate.json, report_session stub removal, build_status_output signature, step_actuals schema, ServerConfig.ensure_dirs(), MCP list_tools return type
- CLAUDE.md-only gotchas preserved: Worktree working directory, README.md location, calibration/ is gitignored, enforcement hooks behavior details, sys.path.insert pattern, importlib pattern, isError always False detail
- Organized by category: Shell & File Paths, Python Testing, Python Package & Imports, MCP SDK Behavior, API Design, CI & Continuous Integration

### US-DR-04: Create docs/plans/index.md

**As** a planning agent, **I want** a plan index **so that** I can see what plans exist, their status, and which feature they belong to.

**Acceptance Criteria:**
- File exists at `docs/plans/index.md`
- Contains a table with columns: Plan File, Feature/Epic, Status (completed/active/superseded), Date
- Lists all 18 plan files currently in `docs/plans/`
- Status is accurate: Phase 1 plans (us-1b*, us-1c*) are "completed", ci-fix plans are "active", pipeline-enforcement plans are "completed"
- Includes the superpowers plan files under `docs/superpowers/plans/` with a note about their location

### US-DR-05: Move marketing docs to docs/marketing/

**As** an engineer, **I want** marketing docs separated from engineering docs **so that** I don't wade through reddit posts and enterprise strategy when looking for technical references.

**Acceptance Criteria:**
- Directory `docs/marketing/` exists
- The following files are moved (git mv):
  - `docs/reddit-feedback-analysis.md` -> `docs/marketing/reddit-feedback-analysis.md`
  - `docs/reddit-technical-response.md` -> `docs/marketing/reddit-technical-response.md`
  - `docs/reddit-final-response.md` -> `docs/marketing/reddit-final-response.md`
  - `docs/enterprise-strategy.md` -> `docs/marketing/enterprise-strategy.md`
  - `docs/enterprise-strategy-adversarial-report.md` -> `docs/marketing/enterprise-strategy-adversarial-report.md`
  - `docs/enterprise-strategy-review-questions.md` -> `docs/marketing/enterprise-strategy-review-questions.md`
  - `docs/enterprise-strategy-v2.md` -> `docs/marketing/enterprise-strategy-v2.md`
- No other files are moved
- No references to these files in other docs are broken (check: MEMORY.md, CLAUDE.md, docs/wiki/ — none currently reference these files)

### US-DR-06: Slim CLAUDE.md

**As** a new-session agent, **I want** CLAUDE.md to be a concise operational reference **so that** I get oriented quickly without reading 150 lines of architecture detail.

**Acceptance Criteria:**
- CLAUDE.md retains these sections unchanged: Repo, Key Files, Hook Enforcement, Test Commands, Memory / Docs Update Paths, Project-Specific Estimate Overrides
- "Architecture Conventions" section (lines 87-113) is replaced with a single line: `See [docs/architecture.md](docs/architecture.md) and [docs/conventions.md](docs/conventions.md).`
- "Gotchas" section (lines 126-148) is replaced with a single line: `See [docs/gotchas.md](docs/gotchas.md).`
- Total CLAUDE.md length is under 90 lines (down from 152)
- The trailing HTML comment (line 152) is preserved
- "Memory / Docs Update Paths" section is updated to include the new docs files (architecture.md, conventions.md, gotchas.md) as update targets

### US-DR-07: Slim MEMORY.md

**As** a new-session agent, **I want** MEMORY.md to contain only project state, decisions, and cost history **so that** it doesn't duplicate architecture/gotchas from CLAUDE.md.

**Acceptance Criteria:**
- MEMORY.md retains: Project Overview, Phase 1 Completion, Decisions & Overrides, Session Cost History, Completed This Session, Next Session Work, Phase 2 Backlog, Related Documentation Files
- "Key Paths" section (lines 37-82) is removed — this duplicates the Key Files table in CLAUDE.md
- "Architecture Conventions" section (lines 84-110) is removed — now in docs/architecture.md
- "Gotchas" section (lines 138-166) is removed — now in docs/gotchas.md
- A "Reference Docs" section is added with pointers to docs/architecture.md, docs/conventions.md, docs/gotchas.md
- Total MEMORY.md length is under 100 lines (down from 196)

### US-DR-08: Delete stale memory files

**As** a developer, **I want** stale plan files removed **so that** they don't confuse future agents.

**Acceptance Criteria:**
- Deleted: `/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/v1.2-plan.md`
- Deleted: `/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/v1.4-plan.md`
- No other memory files are deleted
- Remaining memory files: MEMORY.md, feedback_orchestrator_no_inline.md, feedback_use_codebase_memory.md, decisions_phase1_open_questions.md, project_ci_fix_plan.md, project_hook_enforcement.md

---

## 3. Constraints & Anti-Goals

### Constraints
- **No code changes.** No files in `src/`, `tests/`, `scripts/`, or `.claude/hooks/` are modified.
- **No test changes.** No test files are added, modified, or removed.
- **Operational sections stay in CLAUDE.md.** Key Files, Test Commands, Hook Enforcement, Memory / Docs Update Paths, and Project-Specific Estimate Overrides must remain inline in CLAUDE.md because they are consulted every session by every agent.
- **Memory file format.** MEMORY.md must remain compatible with the auto-memory system (plain markdown, H2 sections).
- **git mv for moves.** Marketing doc moves must use `git mv` to preserve history.

### Anti-Goals
- **Do not reorganize docs/wiki/.** The wiki pages have their own structure and are published separately.
- **Do not reorganize references/.** The heuristics/pricing/calibration-algorithm/examples files are fine where they are.
- **Do not reorganize docs/superpowers/.** This legacy directory can stay as-is; just reference it in the plan index.
- **Do not create docs/decisions.md.** Decisions live in MEMORY.md (they are project state, not reference docs).
- **Do not refactor the Key Files table.** It is operational and stays in CLAUDE.md even though it is long.
- **Do not update README.md.** This is the PyPI package README; it has its own audience.
- **Do not create an automated doc-generation system.** This is a one-time manual restructuring.

---

## 4. Edge Cases & Error States

| Scenario | Handling |
|----------|----------|
| Content appears in both CLAUDE.md and MEMORY.md with slightly different wording | Keep the more detailed/accurate version. When both add unique detail, merge into one bullet. |
| A bullet could be either "architecture" or "convention" | Architecture = structural decisions about how systems are built. Conventions = rules to follow when writing code. When ambiguous, put in architecture (it is the broader category). |
| A gotcha is actually a convention (e.g., "always use /usr/bin/python3") | If it is phrased as "do X to avoid Y", it is a gotcha. If it is phrased as "always do X", it is a convention. Some items may appear in both with different framing. |
| CLAUDE.md references to line numbers in architecture section from hooks | Verified: hooks reference CLAUDE.md by name in comments only, not by line number or section heading. No breakage risk. |
| docs-updater agent needs to know about new files | The "Memory / Docs Update Paths" section in CLAUDE.md will be updated to list the new files. |
| Future sessions load stale CLAUDE.md from context cache | Not an issue — CLAUDE.md is read fresh each session. |

---

## 5. Deferred Decisions

- **docs/superpowers/ reorganization** — the superpowers directory predates the current structure. Leave it for a future cleanup pass.
- **MEMORY.md "Key Paths" content** — some of this is useful for quick orientation. If agents struggle without it, consider adding a "Quick Reference" section back in a future iteration.
- **Automated drift detection between CLAUDE.md and docs/** — could add a test that checks for duplication. Not needed now since this is a one-time cleanup.

---

## 6. Open Questions

None. The scope is fully defined by the gap analysis, constraints are clear, and all content has been inventoried from the actual files.

---

## 7. Scope Summary

| Item | Action |
|------|--------|
| `docs/architecture.md` | Create (consolidate from CLAUDE.md + MEMORY.md) |
| `docs/conventions.md` | Create (extract from CLAUDE.md + MEMORY.md) |
| `docs/gotchas.md` | Create (consolidate from CLAUDE.md + MEMORY.md) |
| `docs/plans/index.md` | Create (index 18 plan files + superpowers reference) |
| `docs/marketing/` | Create directory, `git mv` 7 files |
| `CLAUDE.md` | Slim: replace Architecture Conventions + Gotchas with pointers, update docs paths |
| `MEMORY.md` | Slim: remove Key Paths + Architecture + Gotchas, add Reference Docs pointers |
| `memory/v1.2-plan.md` | Delete |
| `memory/v1.4-plan.md` | Delete |

**Total files created:** 4
**Total files modified:** 2
**Total files moved:** 7
**Total files deleted:** 2
