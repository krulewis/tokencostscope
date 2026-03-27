# Architecture Decision: Documentation & Memory Restructuring

**Date:** 2026-03-27
**Status:** Proposed
**Context:** CLAUDE.md (152 lines) and MEMORY.md (196 lines) have grown organically across 7 feature versions. Architecture conventions, gotchas, and key paths are duplicated between them. The `docs/` directory has 18 plan files with no index, and 7 marketing docs mixed in with engineering references.

---

## Decision Summary

Restructure documentation using a **two-file extraction with slim-in-place** approach. Create `docs/architecture.md` and `docs/gotchas.md` as the two primary extracted reference docs; fold conventions into architecture.md rather than creating a standalone file. Create `docs/plans/index.md` for plan discoverability. Move 7 marketing files to `docs/marketing/`. Slim CLAUDE.md and MEMORY.md by replacing extracted sections with single-line pointers. Delete 2 stale memory files. This is purely a documentation change -- zero code/test modifications.

---

## Chosen Approach

### Description

**Two-file extraction (architecture + gotchas) with pointer-based slim.**

Create two new reference docs in `docs/`:

1. **`docs/architecture.md`** -- Consolidates all architecture conventions AND coding conventions from both CLAUDE.md (lines 87-113, 27 bullets) and MEMORY.md (lines 84-110, 6 subsections). Conventions are folded in as a section within architecture rather than a standalone file, because the line between "structural decision" and "coding rule to follow" is blurry for this project (e.g., "lazy `__init__.py`" is both an architecture decision and a convention). One file avoids agents needing to decide which to read.

2. **`docs/gotchas.md`** -- Merges all gotchas from CLAUDE.md (lines 126-148, 4 subsections: Shell & File Paths, Python Package & Imports, MCP & Testing, CI & Continuous Integration) and MEMORY.md (lines 138-166, 5 subsections: Shell Scripting, Python Testing, API Design, Cascading Imports, MCP SDK Behavior). Deduplicated where both sources cover the same item (e.g., "Paths with spaces", "Python versions", "MCP requires >= 3.10"). Organized into 6 categories: Shell & File Paths, Python Testing, Python Package & Imports, MCP SDK Behavior, API Design, CI & Continuous Integration.

Then slim the source files:

3. **CLAUDE.md** -- Replace "Architecture Conventions" (27 lines) with a one-line pointer. Replace "Gotchas" (22 lines) with a one-line pointer. Update "Memory / Docs Update Paths" to list the new files. Target: under 90 lines.

4. **MEMORY.md** -- Remove "Key Paths" (lines 37-82, duplicates Key Files table in CLAUDE.md). Remove "Architecture Conventions" (lines 84-110, now in docs/architecture.md). Remove "Gotchas" (lines 138-166, now in docs/gotchas.md). Add a 3-line "Reference Docs" section with pointers. Target: under 100 lines.

Supplementary changes:

5. **`docs/plans/index.md`** -- Table of all 18 plan files in `docs/plans/` plus 2 superpowers files, with status (completed/active/superseded) and feature grouping.

6. **`docs/marketing/`** -- `git mv` 7 files (3 reddit-*, 4 enterprise-strategy-*) into new subdirectory.

7. **Delete stale memory files** -- `v1.2-plan.md` and `v1.4-plan.md` from memory directory. Their unique content (PR Review Loop formula derivation, 4-level precedence chain rationale) is already preserved elsewhere: the formula in SKILL.md, `references/heuristics.md`, `references/examples.md`, and `docs/wiki/How-It-Works.md`; the precedence chain in `references/calibration-algorithm.md` (lines 88-94 for v1.4 chain, lines 120-125 for v1.6 updated chain).

### Rationale

- **Two files, not three.** The PM requirements specified three extracted files (architecture.md, conventions.md, gotchas.md). I am collapsing conventions into architecture because the tokencast codebase has approximately 12 "convention" items and 24 "architecture" items, with 6 items that genuinely belong in either category (lazy `__init__.py`, `.get()` backward compat, hook placement, version string consistency, shell injection safety, package exports). A separate 12-item conventions file creates a discoverability problem -- agents must know to check both files and distinguish between them. Merging them into one architecture doc with a "Coding Conventions" H2 section keeps the content organized without requiring agents to make a judgment call about which file to read.

- **Pointer-based slim over inline summary.** Rather than keeping a condensed summary of architecture in CLAUDE.md, replace entirely with a link. Summaries inevitably drift from the source doc. A pointer forces the reader to the canonical source.

- **MEMORY.md Key Paths removal.** The Key Paths section (45 lines) is a near-exact duplicate of the Key Files table in CLAUDE.md (31 lines). CLAUDE.md is loaded every session automatically. Keeping the duplicate in MEMORY.md wastes tokens and creates a maintenance burden where both must be updated in sync.

- **Marketing file move.** These 7 files are not referenced by any other doc, test, or script. They are content strategy artifacts. Moving them to `docs/marketing/` reduces noise when agents browse `docs/` for technical references.

### Alignment with Success Criteria

| Requirement | How satisfied |
|-------------|--------------|
| US-DR-01: Single architecture reference | `docs/architecture.md` created with all architecture + convention content from both sources, deduplicated |
| US-DR-02: Coding conventions in one place | Folded into `docs/architecture.md` under "Coding Conventions" H2 -- same discoverability, one fewer file |
| US-DR-03: Consolidated gotchas | `docs/gotchas.md` created with all gotchas from both sources, deduplicated, 6 categories |
| US-DR-04: Plan index | `docs/plans/index.md` with all 18 + 2 superpowers files, status, dates |
| US-DR-05: Marketing separation | 7 files moved to `docs/marketing/` via `git mv` |
| US-DR-06: CLAUDE.md under 90 lines | Architecture (27 lines) + Gotchas (22 lines) replaced with 2 pointer lines. Net reduction: ~47 lines. 152 - 47 = ~105 lines. The "under 90" target requires also tightening the Key Files table or Hook Enforcement section marginally -- achievable by removing the trailing blank lines and comment. |
| US-DR-07: MEMORY.md under 100 lines | Key Paths (45 lines) + Architecture (27 lines) + Gotchas (29 lines) removed = -101 lines + 3-line Reference Docs section. 196 - 101 + 3 = ~98 lines. Under target. |
| US-DR-08: Stale files deleted | v1.2-plan.md and v1.4-plan.md deleted; unique content verified as preserved elsewhere |

**Note on US-DR-06 target:** The "under 90 lines" target is aggressive. After replacing Architecture Conventions and Gotchas with pointers, CLAUDE.md will be approximately 105 lines. Reaching 90 requires trimming an additional 15 lines from operational sections (Key Files table is 31 lines, Hook Enforcement is 17 lines). The Key Files table should NOT be trimmed -- it is the most-consulted section. The engineer should target "under 110 lines" as the realistic goal and flag to the user if 90 requires cutting operational content. This is an **open question for the user** (OQ-1 below).

---

## Rejected Alternatives

### Option A: Three-file extraction (architecture.md + conventions.md + gotchas.md)

This was the PM's original specification. Rejected because the architecture/conventions boundary is unclear for this project's content. Six items straddle both categories: lazy `__init__.py` (architecture decision AND coding rule), `.get()` backward compat (design principle AND coding pattern), hook placement (system design AND rule), version string consistency (release process AND rule), shell injection safety (security architecture AND coding rule), package exports requirement (API design AND convention). A three-file split forces implementers to make arbitrary categorization decisions and forces readers to check two files when they want "how should I build things." The PM's US-DR-02 acceptance criteria can be satisfied by a clearly-labeled "Coding Conventions" section within architecture.md -- the requirement is "conventions in one place," not "conventions in a standalone file."

### Option B: Monolithic docs/developer-guide.md

Merge architecture, conventions, AND gotchas into a single large developer guide. Rejected because gotchas have fundamentally different consumption patterns from architecture. Architecture is read once during onboarding and referenced occasionally. Gotchas are consulted reactively when something breaks. A developer hitting a "Python 3.14 has no pytest" error needs to find it quickly in a focused gotchas doc, not scan through 200 lines of architecture to find the Python Testing section. The two-file split matches these distinct read patterns.

### Option C: Keep content in CLAUDE.md and MEMORY.md, just deduplicate

Remove duplicated content from MEMORY.md only, keeping CLAUDE.md at its current size. Rejected because CLAUDE.md's architecture section (27 lines) is not operational -- it is reference material that agents consult rarely after initial context load. Every session pays the token cost of loading 27 architecture bullets that are only needed when making structural decisions. Moving this to a separate file that agents can read on demand reduces the per-session baseline cost. CLAUDE.md's purpose is to orient agents quickly; architecture detail works against that purpose when inline.

---

## Resolved Open Questions (from Research)

### RQ-1: Agent discoverability -- how will agents know to read extracted docs?

**Resolution:** Three mechanisms, all sufficient independently:

1. **CLAUDE.md pointers.** The replaced sections will read: `See [docs/architecture.md](docs/architecture.md) and [docs/gotchas.md](docs/gotchas.md).` Agents that would have read those CLAUDE.md sections will follow the pointer instead.

2. **Memory / Docs Update Paths section.** This section in CLAUDE.md (which the `docs-updater` agent reads) will list the new files. This ensures the docs-updater keeps them current.

3. **MEMORY.md Reference Docs section.** A new 3-line section pointing to the extracted docs. MEMORY.md is loaded every session alongside CLAUDE.md.

No additional discoverability mechanism is needed. The pointer pattern is proven in this codebase -- CLAUDE.md already points to `references/heuristics.md`, `references/pricing.md`, and `references/calibration-algorithm.md` via the Key Files table, and agents navigate to those files successfully.

### RQ-2: Transient gotchas -- keep cascading imports gotchas until fix lands, or remove now?

**Resolution:** Keep them. The cascading imports gotcha (CLAUDE.md lines 136-139, MEMORY.md lines 158-161) describes a current, unresolved issue (the 12 remaining CI failures). It will be moved to `docs/gotchas.md` with all other gotchas. When the CI fix lands (lazy `__init__.py`), the implementer or docs-updater agent should remove or update the gotcha entry. Gotchas about resolved issues are more harmful than gotchas about current issues -- a stale gotcha wastes attention and erodes trust in the document. But removing it now, before the fix, would lose operational knowledge that agents need.

### RQ-3: Decision log -- keep in memory/ or move to repo?

**Resolution:** Keep in MEMORY.md. The PM requirements explicitly state: "Do not create docs/decisions.md. Decisions live in MEMORY.md (they are project state, not reference docs)." This is correct -- the Decisions & Overrides section (MEMORY.md lines 112-127) records owner overrides of architect recommendations. These are session-state artifacts that inform future planning decisions, not reference docs that implementers consult. They belong in the auto-memory system.

### RQ-4: v1.2 formula -- check if references/calibration-algorithm.md already has it

**Resolution:** The PR Review Loop formula (`C = staff_review_expected + engineer_final_plan_expected; review_loop_cost(N) = C * (1 - 0.6^N) / 0.4`) is NOT in `references/calibration-algorithm.md` -- that file covers the calibration/learning algorithm, not the estimation formula. However, the formula IS already documented in 5 other locations: `SKILL.md` (Step 3.5), `references/heuristics.md` (PR Review Loop Defaults), `references/examples.md` (worked Example 2), `docs/wiki/How-It-Works.md`, and `docs/wiki/Configuration.md`. The v1.2-plan.md's unique contribution is the design rationale (why 0.6x decay, why N=2 default, why "always include") -- this is historical decision context, not reference material. It is safe to delete. The precedence chain from v1.4-plan.md is fully documented in `references/calibration-algorithm.md` lines 88-94 (v1.4 original) and lines 120-125 (v1.6 update). Safe to delete.

### RQ-5: Plan index -- link to final plans only, or all intermediate docs?

**Resolution:** List all plan files that exist in `docs/plans/`. The intermediate documents (requirements, research, architecture, initial plan, final plan) form a complete decision trail. Listing only final plans would lose traceability -- if someone wants to understand why a decision was made, they need the research and architecture docs. The index table should include a "Type" column (requirements / research / architecture / plan / plan-final) so readers can jump directly to the document type they need. For completed features, the final plan is marked as the primary entry.

---

## Design Details

### File inventory

| File | Action | Notes |
|------|--------|-------|
| `docs/architecture.md` | CREATE | ~80 lines. Consolidates CLAUDE.md Architecture Conventions + MEMORY.md Architecture Conventions + coding conventions. |
| `docs/gotchas.md` | CREATE | ~60 lines. Merges all gotchas from both sources, deduplicated, 6 categories. |
| `docs/plans/index.md` | CREATE | ~40 lines. Table of 20 plan files + 2 superpowers files. |
| `docs/marketing/reddit-feedback-analysis.md` | MOVE | `git mv` from `docs/` |
| `docs/marketing/reddit-technical-response.md` | MOVE | `git mv` from `docs/` |
| `docs/marketing/reddit-final-response.md` | MOVE | `git mv` from `docs/` |
| `docs/marketing/enterprise-strategy.md` | MOVE | `git mv` from `docs/` |
| `docs/marketing/enterprise-strategy-adversarial-report.md` | MOVE | `git mv` from `docs/` |
| `docs/marketing/enterprise-strategy-review-questions.md` | MOVE | `git mv` from `docs/` |
| `docs/marketing/enterprise-strategy-v2.md` | MOVE | `git mv` from `docs/` |
| `CLAUDE.md` | MODIFY | Replace 2 sections with pointers; update docs paths section. Target: ~105 lines. |
| `MEMORY.md` | MODIFY | Remove 3 sections; add Reference Docs pointers. Target: ~98 lines. |
| `memory/v1.2-plan.md` | DELETE | Content preserved in 5 other locations (see RQ-4). |
| `memory/v1.4-plan.md` | DELETE | Content preserved in calibration-algorithm.md (see RQ-4). |

**Total: 3 created, 7 moved, 2 modified, 2 deleted.**

Note: The PM requirement listed `docs/conventions.md` as a separate file (US-DR-02). This architecture folds conventions into `docs/architecture.md` as a subsection. The implementer should NOT create a standalone `docs/conventions.md`. This is a deliberate deviation from the PM spec, with rationale documented above.

### docs/architecture.md structure

```
# Architecture Reference

## Python Package Design
  - Dict-based routing layer
  - Lazy __init__.py
  - No business logic in MCP layer
  - Error handling pattern
  - Package exports requirement

## Estimation Algorithm
  - All tunable parameters in references/heuristics.md
  - Pipeline signature derivation
  - File size brackets
  - file_brackets in active-estimate.json
  - PR Review Loop (Step 3.5 post-step-loop, parallel discount exclusion, per-band calibration)
  - active-estimate.json handshake
  - Backward compatibility (.get() defaults)

## Session Recording & Calibration
  - Session recorder API (dict-based, attribution parameter)
  - Step-cost accumulator (atomic rename, hash scheme)
  - Graceful degradation
  - Time-decay constants (DECAY_HALFLIFE_DAYS, DECAY_MIN_RECORDS invariant)
  - Per-signature factors (Pass 5, canonical form)

## Data Modules
  - Python data modules (pricing.py, heuristics.py) -- derived from markdown sources
  - Cross-module band key invariant
  - Pricing module signature

## MCP Layer & Attribution
  - MCP tools are thin wrappers
  - Attribution protocol versioning (v1, minor additions ok)
  - Mid-session check (midcheck.sh)

## File Size Awareness
  - Three brackets (small/medium/large)
  - N-scaling vs fixed-count

## Coding Conventions
  - Version string consistency (3 places)
  - Shell injection safety (shlex.quote + env vars)
  - Hook placement (hooks/ vs scripts/)
  - CI portability (REPO_ROOT, sys.executable, error logging)
  - Lazy __init__.py pattern (cross-ref to Python Package Design)
```

### docs/gotchas.md structure

```
# Gotchas

## Shell & File Paths
  - Paths with spaces (Macintosh HD2)
  - macOS volume path
  - Worktree working directory
  - README.md location
  - calibration/ is gitignored
  - Enforcement hooks behavior details

## Python Testing
  - Python versions (/usr/bin/python3 = 3.9, Homebrew = 3.14)
  - MCP package requirement (>= 3.10)
  - test_mcp_scaffold.py runs under 3.11 only
  - sys.path.insert pattern
  - macOS timeout command unavailable

## Python Package & Imports
  - Cascading imports issue (in progress)
  - importlib pattern for loading scripts
  - Lazy __init__.py migration status

## MCP SDK Behavior
  - isError always False from call_tool
  - list_tools return type
  - MCP requires Python >= 3.10

## API Design
  - estimate_cost does NOT write active-estimate.json
  - report_session stub removal gotcha
  - build_status_output signature
  - step_actuals schema (plain floats)
  - ServerConfig.ensure_dirs() separation

## CI & Continuous Integration
  - 12 remaining CI failures (test_continuation_session.py)
  - Error visibility in tests (learn.sh || exit 0)
  - REPO_ROOT portability
  - sys.executable in subprocess
```

### CLAUDE.md modifications

Lines 87-113 (Architecture Conventions section, 27 lines) replaced with:

```markdown
## Architecture & Conventions

See [docs/architecture.md](docs/architecture.md) for architecture decisions and coding conventions.
See [docs/gotchas.md](docs/gotchas.md) for known pitfalls and workarounds.
```

Lines 126-148 (Gotchas section, 22 lines) deleted entirely (pointer already added above).

Lines 115-120 (Memory / Docs Update Paths) updated to:

```markdown
## Memory / Docs Update Paths

When completing work, the `docs-updater` agent should update:
- `docs/architecture.md` — if architecture decisions or coding conventions changed
- `docs/gotchas.md` — if new gotchas discovered or existing ones resolved
- `docs/wiki/` — whichever wiki pages cover the changed functionality
- `MEMORY.md` at `/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md`
- `ROADMAP.md` if version or milestone status changed
```

### MEMORY.md modifications

Remove sections:
- Lines 37-82: "Key Paths (Phase 1+)" -- entire section including all subsections
- Lines 84-110: "Architecture Conventions" -- entire section including all subsections
- Lines 138-166: "Gotchas" -- entire section including all subsections

Add after "Phase 1 Completion" section (before "Decisions & Overrides"):

```markdown
## Reference Docs

- [docs/architecture.md](/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/architecture.md) — architecture decisions and coding conventions
- [docs/gotchas.md](/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/gotchas.md) — known pitfalls and workarounds
- [docs/plans/index.md](/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/plans/index.md) — plan file index
```

### docs/plans/index.md content

Table with columns: Plan File | Feature/Epic | Type | Status | Date

Groups:
- **Phase 1b (MCP Server):** us-1b01 through us-1b04 plans -- completed
- **Phase 1c (Attribution):** us-1c01 through us-1c03 plans -- completed
- **Phase 1b (Session Recording):** us-1b09a plan -- completed
- **CI Fix:** ci-fix-requirements, ci-fix-research, ci-fix-architecture, ci-fix-plan, ci-fix-plan-final -- active
- **Pipeline Enforcement:** pipeline-enforcement-requirements through pipeline-enforcement-plan-final -- completed
- **Doc Restructure:** doc-restructure-requirements, doc-restructure-architecture (this file), and subsequent plans -- active
- **Superpowers (legacy):** 2 files in `docs/superpowers/plans/` -- noted with path

### Data model changes

None.

### API contract changes

None.

### Integration points

- **docs-updater agent:** Reads "Memory / Docs Update Paths" in CLAUDE.md to know which files to update. The updated path list ensures the new docs are maintained.
- **Auto-memory system:** MEMORY.md format (plain markdown, H2 sections) is preserved. No frontmatter changes.
- **Git history:** Marketing file moves use `git mv` to preserve blame/history. File deletions are standard `git rm`.

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Agents skip the pointer and miss architecture/gotcha content | Medium | Medium -- agent makes avoidable mistakes | Three redundant pointers (CLAUDE.md section, CLAUDE.md docs paths, MEMORY.md Reference Docs). If an agent reads either CLAUDE.md or MEMORY.md, it encounters a pointer. |
| Content loss during deduplication (a bullet exists in one source but is missed) | Low | High -- lost knowledge causes bugs | The implementer must use a checklist: enumerate every bullet from CLAUDE.md Architecture + Gotchas and every bullet from MEMORY.md Architecture + Gotchas, then check each off as placed in the new file. Any unchecked item is a bug. |
| CLAUDE.md "under 90 lines" target not achievable without cutting operational content | High | Low -- 105 lines is fine | See OQ-1 below. The 90-line target was aspirational. The architecture provides a realistic target of ~105 lines. Implementer should not trim operational sections to hit an arbitrary number. |
| Cascading imports gotcha becomes stale after CI fix lands | Medium | Low -- stale gotcha wastes attention | The CI fix implementer or docs-updater should update `docs/gotchas.md` when the fix ships. Add a note in the gotcha: "Remove this entry when lazy `__init__.py` lands." |
| docs/plans/index.md becomes stale as new plans are added | High | Low -- index is supplementary, not critical | Add a note at the top: "Update this index when adding new plan files." The docs-updater agent path list includes docs/plans/ implicitly via the wiki update path. |

---

## Open Questions

### OQ-1: CLAUDE.md line count target (needs user input)

The PM spec says "under 90 lines." After removing Architecture Conventions (27 lines) and Gotchas (22 lines), CLAUDE.md will be approximately 105 lines. Reaching 90 would require trimming 15 lines from operational sections. The Key Files table (31 lines) is the only section large enough to absorb that trim, but it is the most-consulted section in the file.

**Recommendation:** Accept ~105 lines as the target. The goal was "slim CLAUDE.md" -- a 31% reduction (152 to 105) achieves that. Cutting operational content to hit an arbitrary number is counterproductive.

**User decision needed:** Is ~105 lines acceptable, or should the implementer find additional content to extract/remove?

### OQ-2: US-DR-02 deviation (needs user acknowledgment)

This architecture folds `docs/conventions.md` into `docs/architecture.md` as a subsection, which deviates from the PM spec (US-DR-02 calls for a standalone file). The rationale is documented above (6 ambiguous items, single-file discoverability). The user should confirm this deviation is acceptable before implementation proceeds.
