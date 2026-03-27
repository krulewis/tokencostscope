# Implementation Plan: Documentation & Memory Restructuring

**Engineer Agent — Final Plan**
**Date:** 2026-03-27
**Architecture Decision:** `docs/plans/doc-restructure-architecture.md`
**Initial Plan:** `docs/plans/doc-restructure-plan.md`
**Change Size:** M

---

## Changes from Initial Plan

This section addresses each staff review finding in order.

**#1 [Critical] — `git mv` fails for 3 untracked reddit files**
The 3 reddit files (`docs/reddit-feedback-analysis.md`, `docs/reddit-technical-response.md`, `docs/reddit-final-response.md`) are untracked (`??` in git status). `git mv` requires the source to be tracked; it will fail for these files. Resolution: Group B now uses plain `mv` + `git add` for the 3 reddit files, and keeps `git mv` only for the 4 tracked enterprise-strategy files. Verification step #5 updated to expect 4 renamed + 3 new files.

**#2 [High] — Pricing module `compute_line_cost()` detail dropped**
CLAUDE.md line 109 describes `compute_line_cost()` in `sum-session-tokens.py` as the JSONL adapter that extracts usage from Claude Code JSONL format and delegates to `compute_cost_from_usage()`. This was omitted from the Data Modules section. Resolution: a fourth bullet is added to the Data Modules section of `docs/architecture.md` covering `compute_line_cost()` and its role as JSONL adapter.

**#3 [High] — MEMORY.md line numbers shift during sequential edits**
The initial plan specified MEMORY.md edits by line number (delete 37-82, 84-110, 138-166). Applying these sequentially without accounting for line-number shifts causes incorrect sections to be deleted. Resolution: Group D now specifies all edits by section header, not line number. An explicit implementer note requires either (a) a single-pass delete using the section headers as anchors, or (b) deleting in reverse order (Gotchas first, then Architecture Conventions, then Key Paths). A backup step is also added.

**#4 [Medium] — Plan index superpowers file count**
The initial plan stated "20 plan files (docs/plans/*.md) plus 2 superpowers files = 21 rows minimum." Actual count: `docs/plans/` currently has 21 files (including `doc-restructure-plan.md`). Adding the final plan (`doc-restructure-plan-final.md`, this document) brings it to 22 files. The superpowers directory has 1 plan file and 1 spec file; only the plan file belongs in the plan index. Resolution: index section updated to list 22 rows from `docs/plans/` + 1 superpowers row = 23 rows total. Row count in verification step #4 corrected.

**#5 [Medium] — Plan index row count off by one**
Follows from #4 above. Verification step #4 now checks for 23 rows (22 docs/plans/ files + 1 superpowers plan file).

**#6 [Medium] — `doc-restructure-plan.md` labeled "plan-final" but it's the initial plan**
In the index table, `doc-restructure-plan.md` was listed with Type "plan-final". Resolution: its Type is corrected to "plan". A new row is added for `doc-restructure-plan-final.md` (this document) with Type "plan-final".

**#7 [Medium] — Rollback order not specified**
The initial plan listed rollback steps by group but did not specify a safe order. Resolution: Rollback Notes now explicitly states that full rollback must proceed in reverse dependency order (D → C → B/A/E). A `cp MEMORY.md MEMORY.md.bak` step is added as a prerequisite before any MEMORY.md edits.

**#8 [Medium] — Time-decay/per-signature placement inconsistent with architecture outline**
The initial plan placed "Time-decay constants" and "Per-signature factors" bullets under the "Estimation Algorithm" H2 in `docs/architecture.md`. These belong in "Session Recording & Calibration" because they govern how calibration data is stored and weighted, not how estimates are computed. Resolution: both bullets are moved to the "Session Recording & Calibration" H2. The Estimation Algorithm section retains the bullets that are purely about the forward estimation calculation.

**#9 [Low] — Version string consistency bullet appears twice**
The initial plan included "Version string must be consistent across 3 places" in both the "Estimation Algorithm" section and the "Coding Conventions" section of `docs/architecture.md`. Resolution: the bullet is removed from Estimation Algorithm; it remains only in Coding Conventions, which is the correct home for a consistency rule.

**#10 [not in findings list — no #10 finding was raised]**
No action required.

**#11 [Low] — Line 152 HTML comment referenced by line number after edits shift it**
Group C referenced the trailing HTML comment by line number (line 152). After the Architecture Conventions and Gotchas sections are removed, that line number no longer holds. Resolution: Group C now references the comment by its content (`<!-- Global pipeline... -->`) rather than by line number.

---

## Overview

This plan restructures project documentation by: creating two new canonical reference docs (`docs/architecture.md`, `docs/gotchas.md`), creating a plan index (`docs/plans/index.md`), moving 7 marketing files to `docs/marketing/`, slimming CLAUDE.md and MEMORY.md to pointer-only format, and deleting 2 stale memory files. Zero code or test changes.

All content from CLAUDE.md Architecture Conventions (lines 87–113) and MEMORY.md Architecture Conventions section is consolidated into `docs/architecture.md`. All content from CLAUDE.md Gotchas (lines 126–148) and MEMORY.md Gotchas section is consolidated into `docs/gotchas.md`, deduplicated across both sources.

**Total: 3 created, 7 moved, 2 modified, 2 deleted.**

---

## Changes

### Group A — Create New Reference Docs (independent; can run in parallel)

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/architecture.md
Lines: new file (~95 lines)
Parallelism: independent
Description: Consolidates architecture conventions and coding conventions from CLAUDE.md
  (lines 87-113, 27 bullets) and MEMORY.md (Architecture Conventions section, 6 subsections).
  Conventions are folded in as a final H2 section rather than a standalone file.
Details:
  - H1: "# Architecture Reference"
  - H2: "## Python Package Design"
    Source: MEMORY.md Python Package Design subsection (all 4 bullets verbatim)
    Items: Dict-based routing layer, Lazy __init__.py, No business logic in MCP layer,
    Error handling pattern, Package exports requirement
    Note: "Package exports requirement" comes from CLAUDE.md line 108 (not in MEMORY.md
    under this section — add it here as the 5th bullet)
  - H2: "## Estimation Algorithm"
    Source: CLAUDE.md lines 89-103 (selected bullets); deduplicate against MEMORY.md
    Items:
      - All tunable parameters in references/heuristics.md (CLAUDE.md line 89)
      - Mid-session check: midcheck.sh PreToolUse, active-estimate.json + session JSONL,
        .midcheck-state format, fail-silent (CLAUDE.md line 92)
      - Pipeline signature derivation: not written to active-estimate.json, derived
        inline in SKILL.md Step 3e (CLAUDE.md line 93)
      - active-estimate.json is the handshake between SKILL.md and learn.sh (CLAUDE.md
        line 95)
      - Backward compatibility: .get() defaults for new fields in active-estimate.json
        and factors.json (CLAUDE.md line 96)
      - File size brackets: three brackets small/medium/large with token values, wc -l
        cap 30, avg_file_lines override, medium fallback (CLAUDE.md line 97)
      - file_brackets in active-estimate.json: aggregate counts not per-file,
        null vs empty-dict distinction (CLAUDE.md line 98)
      - PR Review Loop calibration: per-band, not re-anchored (CLAUDE.md line 100)
      - Step 3.5 runs post-step-loop: cache pre-discount costs during per-step loop
        (CLAUDE.md line 101)
      - Parallel discount does NOT apply to PR Review Loop C value (CLAUDE.md line 102)
    NOTE: "Version string consistency" is NOT in this section — it belongs only in
    Coding Conventions (see finding #9). "Time-decay constants" and "Per-signature
    factors" are NOT in this section — they belong in Session Recording & Calibration
    (see finding #8).
  - H2: "## Session Recording & Calibration"
    Source: MEMORY.md Session Recording & Calibration subsection (3 bullets, expanded form)
    + time-decay and per-signature bullets relocated from Estimation Algorithm (finding #8)
    Items:
      - Time-decay constants: DECAY_HALFLIFE_DAYS=30 in update-factors.py mirrors
        decay_halflife_days in references/heuristics.md. DECAY_MIN_RECORDS=5
        (cold-start guard) is hardcoded in update-factors.py and intentionally NOT in
        heuristics.md — it is a statistical invariant, not user-tunable.
        (CLAUDE.md line 90)
      - Per-signature factors: Pass 5, _canonical_sig, signature_factors key with
        .get() default (CLAUDE.md line 91)
      - Session recorder API: dict-based, attribution parameter, step_actuals_mcp /
        step_actuals_sidecar, all 3 paths produce identical schema (CLAUDE.md line 105
        + MEMORY.md)
      - Step-cost accumulator: atomic rename, {hash}-step-accumulator.json, MD5 first
        12 chars of active-estimate.json path, cleared on report_session or new
        estimate_cost (CLAUDE.md line 106 + MEMORY.md)
      - Graceful degradation: missing calibration_dir not an error, corrupted files
        caught and handled (MEMORY.md)
  - H2: "## Data Modules"
    Source: MEMORY.md Data Modules subsection (3 bullets) + compute_line_cost() detail
    (CLAUDE.md line 109, finding #2)
    Items:
      - Python data modules (pricing.py, heuristics.py) are derived artifacts; markdown
        files are human-editable source of truth
      - Cross-module band key invariant: set(CACHE_HIT_RATES.keys()) ==
        set(BAND_MULTIPLIERS.keys()), enforced by test_cross_module_band_keys
      - Pricing module signature: compute_cost_from_usage(usage: dict, model: str)
        -> float — framework-agnostic cost function used by both sum-session-tokens.py
        (JSONL path) and report_step_cost (MCP path)
      - JSONL adapter: compute_line_cost() in sum-session-tokens.py extracts usage from
        Claude Code JSONL format and delegates to compute_cost_from_usage(). This is the
        integration point between the JSONL parsing path (learn.sh) and the pricing module.
        (CLAUDE.md line 109, added per finding #2)
  - H2: "## MCP Layer & Attribution"
    Source: CLAUDE.md lines 103-104 + MEMORY.md backward compat subsection
    Items:
      - Attribution protocol (v1): docs/attribution-protocol.md is source of truth,
        attribution_protocol_version: 1, minor additions ok, rename/removal increments
        version (CLAUDE.md line 103)
      - MCP tools are thin wrappers: delegates to src/tokencast/, no business logic in
        MCP layer (CLAUDE.md line 104)
      - Backward compatibility: new fields use .get() defaults, attribution protocol v1
        allows new optional fields without version bump (MEMORY.md)
  - H2: "## File Size Awareness"
    Source: MEMORY.md File Size Awareness subsection
    Items:
      - Three brackets: small (<=49) = 3k/1k, medium (50-500) = 10k/2.5k,
        large (>=501) = 20k/5k
      - N-scaling vs fixed-count: Implementation and Test Writing use per-bracket sums;
        Research, Engineer, QA use weighted-average read tokens x fixed multiplier
  - H2: "## Coding Conventions"
    Source: CLAUDE.md lines 94, 99, 107, 108, 110-113 (items that are conventions
    rather than pure architecture); also lazy __init__.py cross-reference
    Items:
      - Version string consistency: 3 places must match — SKILL.md frontmatter,
        output template header, learn.sh VERSION variable. Always update all three
        together. (SINGLE occurrence — removed from Estimation Algorithm per finding #9)
      - Shell injection safety: shlex.quote() and env vars pattern in learn.sh and
        midcheck.sh; never interpolate user-derived strings into shell commands
      - Hook placement: enforcement hooks live in .claude/hooks/ (not scripts/).
        Core tokencast functionality stays in scripts/. Use bash '/absolute/path/...'
        in settings.json to handle space in "Macintosh HD2"
      - Package exports: estimate_cost and report_session must be importable from
        tokencast/__init__.py for CI/CD usage without MCP layer
      - Lazy __init__.py pattern: __getattr__-based lazy loading prevents cascading
        imports; preserves `from tokencast import estimate_cost` for end users
        (cross-reference: see Python Package Design section)
      - CI portability — REPO_ROOT: use Path(__file__).resolve().parent.parent.parent
        consistently; never use relative paths
      - CI portability — sys.executable: use sys.executable not bare python3 when
        spawning subprocesses from tests
      - CI portability — error logging: capture stderr from Python subprocesses and
        log before exiting; reduce 2>/dev/null redirections so failures surface
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/gotchas.md
Lines: new file (~80 lines)
Parallelism: independent
Description: Merges all gotchas from CLAUDE.md (lines 126-148, 4 subsections) and
  MEMORY.md (Gotchas section, 5 subsections). Deduplicated. Organized into 6 categories.
  Items that appear in both sources are merged into one entry (the fuller version wins).
Details:
  - H1: "# Gotchas"
  - Opening note: "Update this file when new gotchas are discovered or existing ones
    are resolved. Remove entries when the underlying issue is fixed."
  - H2: "## Shell & File Paths"
    Source: CLAUDE.md lines 129-134 (all 6 bullets); MEMORY.md Shell Scripting subsection
    Merged items:
      - Paths with spaces: always quote; -print0 | xargs -0 for find pipelines;
        repo at /Volumes/Macintosh HD2/... (CLAUDE.md + MEMORY.md merged — use
        CLAUDE.md version as fuller)
      - macOS volume path: /Volumes/Macintosh HD2/... is working dir, space in absolute
        path (CLAUDE.md, unique)
      - Worktree working directory: working dir differs from main repo root, use
        absolute paths (CLAUDE.md, unique)
      - README.md location: repo root, not inside .claude/skills/tokencast/
        (CLAUDE.md, unique)
      - calibration/ is gitignored: do not commit; directory may not exist on fresh
        clone, scripts must handle gracefully (CLAUDE.md, unique)
      - macOS timeout command: not available by default; tests use fake_home + HOME
        override instead of stdin (MEMORY.md, unique)
      - midcheck.sh JSONL discovery: use active-estimate.json mtime as -newer reference
        (not directory mtime); wrap discovery in if [ -f "$ESTIMATE_FILE" ]
        (MEMORY.md, unique)
      - Enforcement hooks: TOKENCAST_SKIP_GATE=1; inline-edit-guard suppresses in
        sub-agent context; branch-guard || true in detached HEAD; validate-agent-type
        fail-open; estimate-gate env overrides for test isolation (CLAUDE.md, unique)
  - H2: "## Python Testing"
    Source: CLAUDE.md lines 141-144 (MCP & Testing); MEMORY.md Python Testing subsection
    Merged items:
      - Python versions: /usr/bin/python3 = 3.9.6 (has pytest), Homebrew python3 =
        3.14 (no pytest); always use /usr/bin/python3 -m pytest (both sources — combined)
      - MCP package requirement: mcp >= 3.10; tests skip cleanly via
        pytest.importorskip("mcp") on 3.9 (CLAUDE.md + MEMORY.md merged)
      - test_mcp_scaffold.py runs under 3.11 only: python3.11 -m pytest
        tests/test_mcp_scaffold.py; do NOT try under /usr/bin/python3 (both sources)
      - sys.path.insert pattern: sys.path.insert(0, str(Path(__file__).parent.parent /
        "src")); must be placed BEFORE pytest.importorskip("mcp") under Python 3.11
        (CLAUDE.md + MEMORY.md merged)
  - H2: "## Python Package & Imports"
    Source: CLAUDE.md lines 136-139; MEMORY.md Cascading Imports subsection
    Merged items:
      - Cascading imports issue (in progress): tokencast/__init__.py eagerly imports
        everything, triggering full MCP dependency tree; learn.sh subprocess can't
        import session_recorder alone. Fix: lazy __getattr__-based loading. After fix:
        revert importlib hacks in learn.sh and sum-session-tokens.py.
        NOTE: Remove this entry when lazy __init__.py lands.
        (CLAUDE.md + MEMORY.md merged into one entry)
      - importlib pattern for loading scripts: sum-session-tokens.py and learn.sh use
        importlib to load from scripts/; workaround for cascading imports
        (CLAUDE.md, unique)
  - H2: "## MCP SDK Behavior"
    Source: CLAUDE.md line 144; MEMORY.md MCP SDK Behavior subsection
    Merged items:
      - isError always False from call_tool: server catches ValueError, returns
        TextContent with error text; isError is always False (SDK does not convert).
        Check error text in ctr.content[0].text, not isError
        (CLAUDE.md + MEMORY.md merged)
      - list_tools return type: list[Tool] (not ListToolsResult)
        (MEMORY.md, unique)
      - MCP requires Python >= 3.10: mcp package cannot be installed on 3.9.
        See Python Testing section for version requirements.
        (cross-reference only — avoid duplicating the full entry from Python Testing)
  - H2: "## API Design"
    Source: MEMORY.md API Design subsection (all 5 bullets, unique to MEMORY.md)
    Items:
      - estimate_cost does NOT write active-estimate.json: MCP tool handler writes it;
        E2E tests use _make_active_estimate() helper
      - report_session stub removal gotcha: old stub returned {"recorded": False,
        "_stub": True}; real handler must NOT return _stub key; tests check
        "_stub" not in result
      - build_status_output signature: build_status_output(all_records, factors,
        verbose=False, window_spec=None, heuristics_path=None); windowing computed
        internally
      - step_actuals schema: values are plain floats (cost in $), not dicts with
        'actual'/'estimated' sub-keys; iteration: for step_name, step_cost in
        r['step_actuals'].items()
      - ServerConfig.ensure_dirs(): directory creation separated from config
        construction; from_args() does NOT create dirs; ensure_dirs() called at
        server startup
  - H2: "## CI & Continuous Integration"
    Source: CLAUDE.md lines 147-148; MEMORY.md (no dedicated CI section beyond Cascading
    Imports)
    Items:
      - 12 remaining CI failures (as of 2026-03-27): all in
        test_continuation_session.py::TestLearnShContinuation x 4 tests x 3 Python
        versions; root cause: cascading imports; fix documented in
        project_ci_fix_plan.md. NOTE: Remove when lazy __init__.py lands.
      - Error visibility in tests: learn.sh uses || exit 0 and 2>/dev/null everywhere;
        tests must capture stderr from _run_learn_sh helper and include in assertion
        failures
      - REPO_ROOT portability: Path(__file__).resolve().parent.parent.parent used
        consistently; never relative paths
      - sys.executable in subprocess: always use sys.executable not bare python3
        when spawning subprocesses from tests
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/plans/index.md
Lines: new file (~60 lines)
Parallelism: independent
Description: Table of all plan files in docs/plans/ (22 files after doc-restructure-plan.md
  and doc-restructure-plan-final.md are both counted) plus 1 superpowers plan file = 23 rows.
  Columns: Plan File | Feature/Epic | Type | Status | Date.
  Status values: completed / active / superseded. Add note at top to update index
  when adding new plan files.
Details:
  - H1: "# Plan Index"
  - Preamble: "Update this index when adding new plan files to docs/plans/."
  - H2: "## Phase 1b — MCP Server (completed)"
    Rows (all status: completed, date: 2026-03-26):
      - us-1b01-plan.md | Extract Estimation Algorithm to Python Engine | plan | completed
      - us-1b02-plan.md | Extract Pricing and Heuristic Data to Python Modules | plan | completed
      - us-1b03-plan.md | MCP Server Scaffold with stdio Transport | plan | completed
      - us-1b04-plan.md | Implement estimate_cost MCP Tool | plan | completed
      - us-1b09a-plan.md | Engine Unit Tests (Additional Coverage) | plan | completed
  - H2: "## Phase 1c — Attribution Decoupling (completed)"
    Rows (all status: completed, date: 2026-03-26):
      - us-1c01-plan.md | Framework-Agnostic Attribution Protocol | plan | completed
      - us-1c02-plan.md | report_step_cost MCP Tool | plan | completed
      - us-1c03-plan.md | Refactor Session Recorder for Multiple Attribution Sources | plan | completed
  - H2: "## CI Fix (active)"
    Rows (date: 2026-03-27):
      - ci-fix-requirements.md | CI Fix: lazy __init__.py + error logging | requirements | active
      - ci-fix-research.md | CI Fix | research | active
      - ci-fix-architecture.md | CI Fix | architecture | active
      - ci-fix-plan.md | CI Fix | plan | active
      - ci-fix-plan-final.md | CI Fix | plan-final | active
  - H2: "## Pipeline Enforcement Hooks (completed)"
    Rows (date: 2026-03-27):
      - pipeline-enforcement-requirements.md | Pipeline Enforcement Hooks | requirements | completed
      - pipeline-enforcement-research.md | Pipeline Enforcement Hooks | research | completed
      - pipeline-enforcement-architecture.md | Pipeline Enforcement Hooks | architecture | completed
      - pipeline-enforcement-plan.md | Pipeline Enforcement Hooks | plan | completed
      - pipeline-enforcement-plan-final.md | Pipeline Enforcement Hooks | plan-final | completed
  - H2: "## Documentation Restructure (active)"
    Rows (date: 2026-03-27):
      - doc-restructure-requirements.md | Doc Restructure | requirements | active
      - doc-restructure-architecture.md | Doc Restructure | architecture | active
      - doc-restructure-plan.md | Doc Restructure | plan | active
        (corrected from "plan-final" — finding #6)
      - doc-restructure-plan-final.md | Doc Restructure | plan-final | active
        (new row for this document — finding #6)
  - H2: "## Superpowers (legacy, docs/superpowers/plans/)"
    Note: "These files are in docs/superpowers/plans/, not docs/plans/."
    Rows:
      - docs/superpowers/plans/2026-03-15-parallel-agent-accounting.md |
        Parallel Agent Accounting | plan | completed | 2026-03-15
  IMPLEMENTER NOTE (finding #4/#5): Total rows = 22 (docs/plans/ files) + 1 (superpowers)
  = 23 rows. Verify this count after writing the file.
```

---

### Group B — Move Marketing Files (independent; can run in parallel with Group A and with each other)

The `docs/marketing/` directory does not yet exist and must be created before any move commands run. The 3 reddit files are **untracked** (`??` in git status) — `git mv` will fail for them. Use plain `mv` + `git add`. The 4 enterprise-strategy files are tracked — use `git mv`. All 7 moves are otherwise independent of each other and of Group A.

```
File: docs/marketing/ (directory + 7 files)
Lines: n/a
Parallelism: independent
Description: Create docs/marketing/ and move 7 files. Use mv+git add for untracked
  reddit files; git mv for tracked enterprise-strategy files. Run all commands in a
  single shell session from repo root.
Details:
  - Run from repo root: /Volumes/Macintosh HD2/Cowork/Projects/costscope
  - Step 1 — create directory:
      mkdir -p docs/marketing
  - Step 2 — move 3 untracked reddit files with mv + git add (finding #1):
      mv docs/reddit-feedback-analysis.md docs/marketing/reddit-feedback-analysis.md
      mv docs/reddit-technical-response.md docs/marketing/reddit-technical-response.md
      mv docs/reddit-final-response.md docs/marketing/reddit-final-response.md
      git add docs/marketing/reddit-feedback-analysis.md
      git add docs/marketing/reddit-technical-response.md
      git add docs/marketing/reddit-final-response.md
  - Step 3 — move 4 tracked enterprise-strategy files with git mv:
      git mv docs/enterprise-strategy.md docs/marketing/enterprise-strategy.md
      git mv docs/enterprise-strategy-adversarial-report.md docs/marketing/enterprise-strategy-adversarial-report.md
      git mv docs/enterprise-strategy-review-questions.md docs/marketing/enterprise-strategy-review-questions.md
      git mv docs/enterprise-strategy-v2.md docs/marketing/enterprise-strategy-v2.md
  - Verify: git status should show 4 renamed files (enterprise-strategy) + 3 new files
    (reddit, previously untracked). No unstaged deletions.
  - None of these 7 files are referenced by any other doc, test, or script (confirmed
    by architecture decision). No reference updates needed.
```

---

### Group C — Modify CLAUDE.md (depends on: Group A complete)

CLAUDE.md must not be modified until `docs/architecture.md` and `docs/gotchas.md` exist, because the modification replaces sections with pointers to those files.

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/CLAUDE.md
Lines: lines 87-end range affected; target ~107 lines total
Parallelism: depends-on: Group A (docs/architecture.md and docs/gotchas.md created)
Description: Replace "Architecture Conventions" section (lines 87-113, 27 lines)
  and "Gotchas" section (lines 126-148, 22 lines + separator) with two pointer lines.
  Update "Memory / Docs Update Paths" to list new files. Delete trailing HTML comment.
Details:
  Replace lines 87-113 (the entire "## Architecture Conventions" section) with:

    ## Architecture & Conventions

    See [docs/architecture.md](docs/architecture.md) for architecture decisions and
    coding conventions.
    See [docs/gotchas.md](docs/gotchas.md) for known pitfalls and workarounds.

  Delete the entire "## Gotchas" section including the subsections and the trailing
  "---" separator that follows it. The pointer added above covers both architecture
  and gotchas, so the Gotchas section header is fully redundant.

  Replace the "## Memory / Docs Update Paths" section with:

    ## Memory / Docs Update Paths

    When completing work, the `docs-updater` agent should update:
    - `docs/architecture.md` — if architecture decisions or coding conventions changed
    - `docs/gotchas.md` — if new gotchas discovered or existing ones resolved
    - `docs/plans/index.md` — if new plan files added to docs/plans/
    - `docs/wiki/` — whichever wiki pages cover the changed functionality
    - `MEMORY.md` at `/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md`
    - `ROADMAP.md` if version or milestone status changed

  Delete the trailing HTML comment that begins with "<!-- Global pipeline..." by
  searching for the comment content rather than by line number. (Finding #11: line
  numbers shift after prior deletions; reference by content, not position.)
  Command to locate it first: grep -n "Global pipeline" CLAUDE.md

  Final structure of CLAUDE.md (in order):
    1. H1 title + project description
    2. ## Repo
    3. ## Key Files table
    4. ## Hook Enforcement
    5. ## Test Commands
    6. ## Architecture & Conventions [MODIFIED — 4 lines replacing 27]
    7. ## Memory / Docs Update Paths [MODIFIED — adds 3 new entries]
    8. ## Project-Specific Estimate Overrides (unchanged)
    [Gotchas section DELETED]
    [Trailing HTML comment DELETED]

  Expected line count: 152 - 27 (Architecture Conventions) + 4 (pointer replacement)
    - 23 (Gotchas section) - 2 (separator + comment) + 3 (new docs paths entries)
    = ~107 lines.
```

---

### Group D — Modify MEMORY.md (depends on: Group A complete)

MEMORY.md must not be modified until `docs/architecture.md`, `docs/gotchas.md`, and `docs/plans/index.md` exist, because the modification adds pointers to all three.

```
File: /Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md
Lines: multiple sections affected; target ~99 lines total
Parallelism: depends-on: Group A (all three new docs created)
Description: Remove "Key Paths (Phase 1+)" section, "Architecture Conventions" section,
  and "Gotchas" section. Add "Reference Docs" section after "Phase 1 Completion".
  Keep "Decisions & Overrides", "Session Cost History", "Next Session Work",
  "Phase 2 Backlog", and "Related Documentation Files" intact.

IMPLEMENTER NOTE — APPLY EDITS BY SECTION HEADER, NOT LINE NUMBER (finding #3):
  Line numbers shift as sections are deleted. Do NOT delete by line range. Instead,
  locate each section to delete by its header string and delete from that header to
  the next H2 header. If deleting sequentially, work in reverse order (Gotchas first,
  then Architecture Conventions, then Key Paths) so that prior deletions do not shift
  lines for subsequent deletes. A single-pass approach using a script is safest.

  PREREQUISITE: Before making any edits, create a backup:
    cp "/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md" \
       "/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md.bak"

Details:
  DELETE: The section with header "## Key Paths (Phase 1+)" — all subsections:
    Shell Skills & Calibration, Python Package: Core Modules, Python Package: MCP
    Server, Tests, Documentation. Delete from "## Key Paths" through the blank line
    before the next H2 header.

  DELETE: The section with header "## Architecture Conventions" — all subsections:
    Python Package Design, Session Recording & Calibration, Data Modules,
    Backward Compatibility, File Size Awareness. Delete from "## Architecture
    Conventions" through the blank line before the next H2 header.

  DELETE: The section with header "## Gotchas" — all subsections:
    Shell Scripting, Python Testing, API Design, Cascading Imports, MCP SDK Behavior.
    Delete from "## Gotchas" through the blank line before the next H2 header.

  INSERT after the last line of "## Phase 1 Completion" section, before "## Decisions
  & Overrides":

    ## Reference Docs

    - [docs/architecture.md](/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/architecture.md) — architecture decisions and coding conventions
    - [docs/gotchas.md](/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/gotchas.md) — known pitfalls and workarounds
    - [docs/plans/index.md](/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/plans/index.md) — plan file index

  Final structure of MEMORY.md (in order):
    1. H1 title
    2. ## Project Overview (unchanged)
    3. ## Phase 1 Completion (unchanged)
    4. ## Reference Docs [NEW — 5 lines]
    5. ## Decisions & Overrides (Phase 1) (unchanged)
    6. ## Session Cost History (unchanged)
    7. ## Next Session Work (unchanged)
    8. ## Phase 2 Backlog (unchanged)
    9. ## Related Documentation Files (unchanged)

  Expected line count: 196 original
    - 46 (Key Paths section)
    - 27 (Architecture Conventions section)
    - 29 (Gotchas section)
    + 5 (Reference Docs section with blank lines)
    = ~99 lines. Under the 100-line target.

  IMPORTANT: The "Next Session Work" section and "Phase 2 Backlog" section must be
  preserved verbatim. These are session-state records, not reference content.
```

---

### Group E — Delete Stale Memory Files (independent)

```
File: /Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/v1.2-plan.md
Lines: delete (41 lines)
Parallelism: independent
Description: Delete stale v1.2 plan memory file. Content verified as preserved
  elsewhere per architecture RQ-4: PR Review Loop formula in SKILL.md Step 3.5,
  references/heuristics.md, references/examples.md, docs/wiki/How-It-Works.md,
  docs/wiki/Configuration.md. Design rationale (why 0.6x decay, N=2 default) is
  historical context, not reference material.
Details:
  - Use standard file deletion; this file is outside the git repo
  - Verify before deleting: grep -r "v1.2-plan" across all .md files to confirm no
    other doc references this file by name
  - Command: rm "/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/v1.2-plan.md"
```

```
File: /Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/v1.4-plan.md
Lines: delete (110 lines)
Parallelism: independent
Description: Delete stale v1.4 plan memory file. Content verified as preserved
  per architecture RQ-4: 4-level precedence chain fully documented in
  references/calibration-algorithm.md lines 88-94 (v1.4 original) and lines 120-125
  (v1.6 updated chain). PR Review Loop exclusion rationale documented there too.
Details:
  - Command: rm "/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/v1.4-plan.md"
  - Verify: grep -r "v1.4-plan" across .md files to confirm no active references
```

---

## Dependency Order

```
Tier 1 (run in parallel — no dependencies):
  - Group A: Create docs/architecture.md
  - Group A: Create docs/gotchas.md
  - Group A: Create docs/plans/index.md
  - Group B: Move 7 marketing files (single shell session, mkdir + mv/git mv)
  - Group E: Delete v1.2-plan.md
  - Group E: Delete v1.4-plan.md

Tier 2 (run after Tier 1 completes — require new docs to exist):
  - Group C: Modify CLAUDE.md
    depends-on: docs/architecture.md and docs/gotchas.md created
  - Group D: Modify MEMORY.md
    depends-on: docs/architecture.md, docs/gotchas.md, docs/plans/index.md created

Tier 2 changes (Group C and Group D) are independent of each other and can run
in parallel once Tier 1 is done.
```

---

## Verification Steps

### After Group A (new docs created)

1. **docs/architecture.md completeness checklist** — every bullet from CLAUDE.md lines 87-113 must appear in the new file. Count: 27 bullets in original. Verify all 27 are present by reading architecture.md and ticking off each CLAUDE.md bullet. Then verify every MEMORY.md Architecture section bullet is present. Any bullet missing from both sources is a content loss bug. Verify time-decay and per-signature bullets appear under "Session Recording & Calibration" (not Estimation Algorithm). Verify version string consistency bullet appears only once, under "Coding Conventions".

2. **docs/gotchas.md completeness checklist** — enumerate every bullet from:
   - CLAUDE.md Gotchas (lines 126-148): 4 subsections, ~17 bullets
   - MEMORY.md Gotchas section: 5 subsections, ~20 bullets
   Merged count should be ~30 unique bullets (7 duplicates collapsed). Verify all unique bullets present.

3. **Line count targets:**
   - `wc -l docs/architecture.md` — expect 80-100 lines
   - `wc -l docs/gotchas.md` — expect 70-90 lines
   - `wc -l docs/plans/index.md` — expect 50-65 lines

4. **docs/plans/index.md row count** — count rows in the table: should have 22 plan files (docs/plans/*.md, including both doc-restructure-plan.md and doc-restructure-plan-final.md) plus 1 superpowers plan file = 23 rows total. (Corrected from initial plan per findings #4 and #5.)

### After Group B (marketing files moved)

5. **git status check:** `git status` should show exactly 4 renamed files (`docs/enterprise-strategy*.md` → `docs/marketing/enterprise-strategy*.md`) plus 3 new files (`docs/marketing/reddit-*.md`, which were previously untracked). No unstaged deletions. (Corrected from initial plan per finding #1.)

6. **No orphaned references:** `grep -r "reddit-feedback-analysis\|reddit-technical-response\|reddit-final-response\|enterprise-strategy" /Volumes/Macintosh\ HD2/Cowork/Projects/costscope --include="*.md" --include="*.py" --include="*.sh"` — any hits outside docs/marketing/ or docs/plans/index.md should be reviewed (none expected per architecture decision).

### After Group C (CLAUDE.md modified)

7. **Line count:** `wc -l /Volumes/Macintosh\ HD2/Cowork/Projects/costscope/CLAUDE.md` — expect 100-110 lines.

8. **Pointer present:** `grep -n "docs/architecture.md\|docs/gotchas.md" CLAUDE.md` — should return lines in the Architecture & Conventions section.

9. **Deleted section gone:** `grep -n "## Architecture Conventions\|## Gotchas" CLAUDE.md` — should return 0 results.

10. **Docs update paths complete:** `grep -n "docs/architecture.md\|docs/gotchas.md\|docs/plans/index.md" CLAUDE.md` — should return hits in the Memory / Docs Update Paths section.

11. **Trailing comment gone:** `grep -n "Global pipeline" CLAUDE.md` — should return 0 results.

### After Group D (MEMORY.md modified)

12. **Line count:** `wc -l "/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md"` — expect 95-105 lines.

13. **Deleted sections gone:** `grep -n "## Key Paths\|## Architecture Conventions\|## Gotchas" MEMORY.md` — should return 0 results.

14. **Reference Docs section present:** `grep -n "## Reference Docs" MEMORY.md` — should return 1 hit.

15. **Preserved sections intact:** Verify "## Decisions & Overrides", "## Session Cost History", "## Next Session Work", "## Phase 2 Backlog", "## Related Documentation Files" all still present.

### After Group E (memory files deleted)

16. **Files gone:** `ls "/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/"` — v1.2-plan.md and v1.4-plan.md should not appear. MEMORY.md.bak may be present (clean up after verification).

### Final cross-check

17. **No cross-references broken:** Search for any remaining references to the deleted memory files: `grep -r "v1.2-plan\|v1.4-plan" /Volumes/Macintosh\ HD2/Cowork/Projects/costscope --include="*.md"` — expect 0 hits.

18. **No test files modified:** `git diff --name-only | grep "tests/"` — expect empty. This is a docs-only change.

19. **No source files modified:** `git diff --name-only | grep "^src/"` — expect empty.

---

## Test Strategy

This change is documentation-only — no Python source, no shell scripts, no test files are modified. Therefore:

- **No new tests to write.** Documentation restructuring does not affect any tested behavior.
- **No existing tests to update.** No test file imports or references any of the files being moved, created, or deleted.
- **Verification is manual** (the checklist steps above): line counts, grep for orphaned references, grep for deleted section headers, confirm pointer links resolve to real files.

Run the existing test suite after changes as a sanity check that nothing was accidentally touched:

```bash
/usr/bin/python3 -m pytest tests/ -q
```

Expected result: same pass/skip counts as before (939 passing, 71 skipped). Any regression indicates an accidental file modification.

---

## Deviations from Architecture Decision

**None.** This plan implements the architecture decision as written, including:

1. **Conventions folded into architecture.md** (not a standalone `docs/conventions.md`): The architecture explicitly calls this out as a deliberate deviation from the PM spec (US-DR-02). This plan follows the architecture, not the PM spec. The "Coding Conventions" H2 in architecture.md satisfies US-DR-02's acceptance criteria ("conventions in one place").

2. **CLAUDE.md target ~107 lines, not 90**: The architecture explicitly acknowledges OQ-1 — the "under 90 lines" PM target is not achievable without cutting operational content. This plan targets ~107 lines (31% reduction from 152), consistent with the architecture's guidance of ~105 as the realistic goal.

3. **docs/plans/index.md includes all plan files, not just final plans**: Architecture RQ-5 resolved this in favor of listing all files with a "Type" column. This plan follows that resolution.

4. **MEMORY.md "Next Session Work" and "Phase 2 Backlog" sections preserved verbatim**: These sections were added after the architecture was written. They are session-state records and fall outside the scope of the restructure. Deleting them would lose operational state about the Pipeline Enforcement Hooks completion and pending CI fix work.

---

## Rollback Notes

This change is documentation-only with no code side effects.

**Rollback must be performed in reverse dependency order: D → C → B/A/E** (finding #7). Modifying Group C or Group D before rolling back creates pointer files referencing non-existent targets. Do not attempt to roll back Tier 1 before rolling back Tier 2.

**Step 0 — MEMORY.md backup prerequisite:** Before beginning Group D, a backup exists at `MEMORY.md.bak`. If rolling back Group D, restore from backup:
```bash
cp "/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md.bak" \
   "/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md"
```

**To rollback Group D (MEMORY.md modified):** Restore from `MEMORY.md.bak` as shown above. MEMORY.md is outside the git repo; `git checkout` does not apply.

**To rollback Group C (CLAUDE.md modified):** `git checkout CLAUDE.md` restores the original. No data is lost — the original Architecture Conventions and Gotchas sections are simply restored.

**To rollback Group B (marketing files moved):** Run reverse commands:
```bash
# For tracked enterprise-strategy files:
git mv docs/marketing/enterprise-strategy.md docs/enterprise-strategy.md
git mv docs/marketing/enterprise-strategy-adversarial-report.md docs/enterprise-strategy-adversarial-report.md
git mv docs/marketing/enterprise-strategy-review-questions.md docs/enterprise-strategy-review-questions.md
git mv docs/marketing/enterprise-strategy-v2.md docs/enterprise-strategy-v2.md
# For untracked reddit files (now tracked after git add):
git rm --cached docs/marketing/reddit-feedback-analysis.md
git rm --cached docs/marketing/reddit-technical-response.md
git rm --cached docs/marketing/reddit-final-response.md
mv docs/marketing/reddit-feedback-analysis.md docs/reddit-feedback-analysis.md
mv docs/marketing/reddit-technical-response.md docs/reddit-technical-response.md
mv docs/marketing/reddit-final-response.md docs/reddit-final-response.md
rmdir docs/marketing/
```

**To rollback Group A (new files created):** Delete `docs/architecture.md`, `docs/gotchas.md`, and `docs/plans/index.md`. These are new files with no prior state.

**To rollback Group E (memory files deleted):** The content of v1.2-plan.md and v1.4-plan.md is preserved in the `references/` directory per architecture RQ-4. The files themselves cannot be un-deleted without a backup, but their unique content is already captured. Before running Group E, confirm by reading `references/calibration-algorithm.md` lines 88-94 and 120-125.

**Recommended commit strategy for safer rollback:** Commit Group B (git mv/mv+add) and Group A (new files) as separate commits from Group C+D (CLAUDE.md and MEMORY.md modifications). This allows reverting CLAUDE.md/MEMORY.md changes without losing the new reference docs.
