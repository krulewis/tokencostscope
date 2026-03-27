# Implementation Plan: Documentation & Memory Restructuring

**Engineer Agent — Initial Plan**
**Date:** 2026-03-27
**Architecture Decision:** `docs/plans/doc-restructure-architecture.md`
**Change Size:** M

---

## Overview

This plan restructures project documentation by: creating two new canonical reference docs (`docs/architecture.md`, `docs/gotchas.md`), creating a plan index (`docs/plans/index.md`), moving 7 marketing files to `docs/marketing/`, slimming CLAUDE.md and MEMORY.md to pointer-only format, and deleting 2 stale memory files. Zero code or test changes.

All content from CLAUDE.md Architecture Conventions (lines 87–113) and MEMORY.md Architecture Conventions (lines 84–110) is consolidated into `docs/architecture.md`. All content from CLAUDE.md Gotchas (lines 126–148) and MEMORY.md Gotchas (lines 138–166) is consolidated into `docs/gotchas.md`, deduplicated across both sources.

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
  (lines 87-113, 27 bullets) and MEMORY.md (lines 84-110, 6 subsections).
  Conventions are folded in as a final H2 section rather than a standalone file.
Details:
  - H1: "# Architecture Reference"
  - H2: "## Python Package Design"
    Source: MEMORY.md lines 86-90 (all 4 bullets verbatim)
    Items: Dict-based routing layer, Lazy __init__.py, No business logic in MCP layer,
    Error handling pattern, Package exports requirement
    Note: "Package exports requirement" comes from CLAUDE.md line 108 (not in MEMORY.md
    under this section — add it here as the 5th bullet)
  - H2: "## Estimation Algorithm"
    Source: CLAUDE.md lines 89-103 (selected bullets); deduplicate against MEMORY.md
    Items:
      - All tunable parameters in references/heuristics.md (CLAUDE.md line 89)
      - Time-decay constants: DECAY_HALFLIFE_DAYS=30, DECAY_MIN_RECORDS=5 invariant
        (CLAUDE.md line 90)
      - Per-signature factors: Pass 5, _canonical_sig, signature_factors key with
        .get() default (CLAUDE.md line 91)
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
      - Version string must be consistent across 3 places (CLAUDE.md line 99)
      - PR Review Loop calibration: per-band, not re-anchored (CLAUDE.md line 100)
      - Step 3.5 runs post-step-loop: cache pre-discount costs during per-step loop
        (CLAUDE.md line 101)
      - Parallel discount does NOT apply to PR Review Loop C value (CLAUDE.md line 102)
  - H2: "## Session Recording & Calibration"
    Source: MEMORY.md lines 92-95 (3 bullets, expanded form)
    Items:
      - Session recorder API: dict-based, attribution parameter, step_actuals_mcp /
        step_actuals_sidecar, all 3 paths produce identical schema (CLAUDE.md line 105
        + MEMORY.md line 93)
      - Step-cost accumulator: atomic rename, {hash}-step-accumulator.json, MD5 first
        12 chars of active-estimate.json path, cleared on report_session or new
        estimate_cost (CLAUDE.md line 106 + MEMORY.md line 94)
      - Graceful degradation: missing calibration_dir not an error, corrupted files
        caught and handled (MEMORY.md line 95)
  - H2: "## Data Modules"
    Source: MEMORY.md lines 97-100 (3 bullets)
    Items:
      - Python data modules (pricing.py, heuristics.py) are derived artifacts; markdown
        files are human-editable source of truth
      - Cross-module band key invariant: set(CACHE_HIT_RATES.keys()) ==
        set(BAND_MULTIPLIERS.keys()), enforced by test
      - Pricing module signature: compute_cost_from_usage(usage: dict, model: str)
        -> float
  - H2: "## MCP Layer & Attribution"
    Source: CLAUDE.md lines 103-104 + MEMORY.md backward compat lines 103-105
    Items:
      - Attribution protocol (v1): docs/attribution-protocol.md is source of truth,
        attribution_protocol_version: 1, minor additions ok, rename/removal increments
        version (CLAUDE.md line 103)
      - MCP tools are thin wrappers: delegates to src/tokencast/, no business logic in
        MCP layer (CLAUDE.md line 104)
      - Backward compatibility: new fields use .get() defaults, attribution protocol v1
        allows new optional fields (MEMORY.md lines 103-105)
  - H2: "## File Size Awareness"
    Source: MEMORY.md lines 107-110
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
        output template header, learn.sh VERSION variable
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
  MEMORY.md (lines 138-166, 5 subsections). Deduplicated. Organized into 6 categories.
  Items that appear in both sources are merged into one entry (the fuller version wins).
Details:
  - H1: "# Gotchas"
  - Opening note: "Update this file when new gotchas are discovered or existing ones
    are resolved. Remove entries when the underlying issue is fixed."
  - H2: "## Shell & File Paths"
    Source: CLAUDE.md lines 129-134 (all 6 bullets); MEMORY.md lines 141-143 (3 bullets)
    Merged items:
      - Paths with spaces: always quote; -print0 | xargs -0 for find pipelines;
        repo at /Volumes/Macintosh HD2/... (CLAUDE.md line 129 + MEMORY.md line 141
        are the same — use CLAUDE.md version as fuller)
      - macOS volume path: /Volumes/Macintosh HD2/... is working dir, space in absolute
        path (CLAUDE.md line 130, unique)
      - Worktree working directory: working dir differs from main repo root, use
        absolute paths (CLAUDE.md line 131, unique)
      - README.md location: repo root, not inside .claude/skills/tokencast/
        (CLAUDE.md line 132, unique)
      - calibration/ is gitignored: do not commit; directory may not exist on fresh
        clone, scripts must handle gracefully (CLAUDE.md line 133, unique)
      - macOS timeout command: not available by default; tests use fake_home + HOME
        override instead of stdin (MEMORY.md line 142, unique to MEMORY.md)
      - midcheck.sh JSONL discovery: use active-estimate.json mtime as -newer reference
        (not directory mtime); wrap discovery in if [ -f "$ESTIMATE_FILE" ]
        (MEMORY.md line 143, unique)
      - Enforcement hooks: TOKENCAST_SKIP_GATE=1; inline-edit-guard suppresses in
        sub-agent context; branch-guard || true in detached HEAD; validate-agent-type
        fail-open; estimate-gate env overrides for test isolation
        (CLAUDE.md line 134, unique)
  - H2: "## Python Testing"
    Source: CLAUDE.md lines 141-144 (MCP & Testing); MEMORY.md lines 145-149
    Merged items:
      - Python versions: /usr/bin/python3 = 3.9.6 (has pytest), Homebrew python3 =
        3.14 (no pytest); always use /usr/bin/python3 -m pytest (both sources — use
        combined)
      - MCP package requirement: mcp >= 3.10; tests skip cleanly via
        pytest.importorskip("mcp") on 3.9 (CLAUDE.md line 142 + MEMORY.md line 147
        merged)
      - test_mcp_scaffold.py runs under 3.11 only: python3.11 -m pytest
        tests/test_mcp_scaffold.py; do NOT try under /usr/bin/python3 (both sources)
      - sys.path.insert pattern: sys.path.insert(0, str(Path(__file__).parent.parent /
        "src")); must be placed BEFORE pytest.importorskip("mcp") under Python 3.11
        (CLAUDE.md line 138 + MEMORY.md line 149 merged)
  - H2: "## Python Package & Imports"
    Source: CLAUDE.md lines 136-139; MEMORY.md lines 158-161
    Merged items:
      - Cascading imports issue (in progress): tokencast/__init__.py eagerly imports
        everything, triggering full MCP dependency tree; learn.sh subprocess can't
        import session_recorder alone. Fix: lazy __getattr__-based loading. After fix:
        revert importlib hacks in learn.sh and sum-session-tokens.py.
        NOTE: Remove this entry when lazy __init__.py lands.
        (CLAUDE.md line 137 + MEMORY.md lines 158-161 merged into one entry)
      - importlib pattern for loading scripts: sum-session-tokens.py and learn.sh use
        importlib to load from scripts/; workaround for cascading imports
        (CLAUDE.md line 139, unique)
  - H2: "## MCP SDK Behavior"
    Source: CLAUDE.md line 144; MEMORY.md lines 163-166
    Merged items:
      - isError always False from call_tool: server catches ValueError, returns
        TextContent with error text; isError is always False (SDK does not convert).
        Check error text in ctr.content[0].text, not isError
        (CLAUDE.md line 144 + MEMORY.md line 164 merged)
      - list_tools return type: list[Tool] (not ListToolsResult)
        (MEMORY.md line 165, unique)
      - MCP requires Python >= 3.10: mcp package cannot be installed on 3.9
        (MEMORY.md line 166 — already covered in Python Testing section; omit here
        to avoid duplication, OR add a cross-reference line)
        IMPLEMENTER NOTE: Add as cross-reference: "See Python Testing section for
        version requirements."
  - H2: "## API Design"
    Source: MEMORY.md lines 151-156 (all 5 bullets, unique to MEMORY.md)
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
    Source: CLAUDE.md lines 147-148; MEMORY.md (no dedicated CI section beyond what
    is in Cascading Imports)
    Items:
      - 12 remaining CI failures (as of 2026-03-27): all in
        test_continuation_session.py::TestLearnShContinuation x 4 tests x 3 Python
        versions; root cause: cascading imports; fix documented in
        project_ci_fix_plan.md. NOTE: Remove when lazy __init__.py lands.
      - Error visibility in tests: learn.sh uses || exit 0 and 2>/dev/null everywhere;
        tests must capture stderr from _run_learn_sh helper and include in assertion
        failures
      - REPO_ROOT portability: Path(__file__).resolve().parent.parent.parent used
        consistently; never relative paths (moved here from CLAUDE.md line 111)
      - sys.executable in subprocess: always use sys.executable not bare python3
        when spawning subprocesses from tests (moved here from CLAUDE.md line 112)
```

---

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/plans/index.md
Lines: new file (~55 lines)
Parallelism: independent
Description: Table of all plan files in docs/plans/ (20 files after this doc is added)
  plus 2 superpowers files. Columns: Plan File | Feature/Epic | Type | Status | Date.
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
      - doc-restructure-plan.md | Doc Restructure | plan-final | active
  - H2: "## Superpowers (legacy, docs/superpowers/plans/)"
    Note: "These files are in docs/superpowers/plans/, not docs/plans/."
    Rows:
      - docs/superpowers/plans/2026-03-15-parallel-agent-accounting.md |
        Parallel Agent Accounting | plan | completed | 2026-03-15
```

---

### Group B — Move Marketing Files (independent; can run in parallel with Group A and with each other)

The `docs/marketing/` directory does not yet exist and must be created before any `git mv` commands run. All 7 moves are otherwise independent of each other and of Group A.

```
File: docs/marketing/ (directory)
Lines: n/a
Parallelism: independent (prerequisite for all Group B moves)
Description: Create the docs/marketing/ directory via the first git mv command.
  git mv will create the parent directory automatically, so no explicit mkdir needed
  if all 7 moves are run as a batch. The implementer should run all 7 git mv commands
  in a single shell session.
Details:
  - Run from repo root: /Volumes/Macintosh HD2/Cowork/Projects/costscope
  - git mv docs/reddit-feedback-analysis.md docs/marketing/reddit-feedback-analysis.md
  - git mv docs/reddit-technical-response.md docs/marketing/reddit-technical-response.md
  - git mv docs/reddit-final-response.md docs/marketing/reddit-final-response.md
  - git mv docs/enterprise-strategy.md docs/marketing/enterprise-strategy.md
  - git mv docs/enterprise-strategy-adversarial-report.md docs/marketing/enterprise-strategy-adversarial-report.md
  - git mv docs/enterprise-strategy-review-questions.md docs/marketing/enterprise-strategy-review-questions.md
  - git mv docs/enterprise-strategy-v2.md docs/marketing/enterprise-strategy-v2.md
  - Verify: git status should show 7 renamed files, no deletions
  - None of these 7 files are referenced by any other doc, test, or script (confirmed
    by architecture decision). No reference updates needed.
```

---

### Group C — Modify CLAUDE.md (depends on: Group A complete)

CLAUDE.md must not be modified until `docs/architecture.md` and `docs/gotchas.md` exist, because the modification replaces sections with pointers to those files.

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/CLAUDE.md
Lines: 87-152 modified; target ~105 lines total
Parallelism: depends-on: Group A (docs/architecture.md and docs/gotchas.md created)
Description: Replace "Architecture Conventions" section (lines 87-113, 27 lines)
  and "Gotchas" section (lines 126-148, 22 lines + separator) with two pointer lines.
  Update "Memory / Docs Update Paths" to list new files.
  Delete trailing comment on line 152.
Details:
  Replace lines 87-113 (the entire "## Architecture Conventions" section) with:

    ## Architecture & Conventions

    See [docs/architecture.md](docs/architecture.md) for architecture decisions and
    coding conventions.
    See [docs/gotchas.md](docs/gotchas.md) for known pitfalls and workarounds.

  Delete lines 126-148 (the entire "## Gotchas" section including the subsections
  and the trailing "---" separator on line 150). The pointer added above covers both
  architecture and gotchas, so the Gotchas section header is fully redundant.

  Replace lines 115-120 ("## Memory / Docs Update Paths" section) with:

    ## Memory / Docs Update Paths

    When completing work, the `docs-updater` agent should update:
    - `docs/architecture.md` — if architecture decisions or coding conventions changed
    - `docs/gotchas.md` — if new gotchas discovered or existing ones resolved
    - `docs/plans/index.md` — if new plan files added to docs/plans/
    - `docs/wiki/` — whichever wiki pages cover the changed functionality
    - `MEMORY.md` at `/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md`
    - `ROADMAP.md` if version or milestone status changed

  Delete line 152 (the HTML comment: "<!-- Global pipeline... -->"). It is stale
  metadata. This shaves 1 line.

  Final structure of CLAUDE.md (in order):
    1. H1 title + project description (lines 1-3)
    2. ## Repo (lines 5-9)
    3. ## Key Files table (lines 11-44)
    4. ## Hook Enforcement (lines 46-65)
    5. ## Test Commands (lines 67-85)
    6. ## Architecture & Conventions [MODIFIED — 4 lines replacing 27]
    7. ## Memory / Docs Update Paths [MODIFIED — adds 3 new entries]
    8. ## Project-Specific Estimate Overrides (unchanged)
    [Gotchas section DELETED]
    [Trailing comment DELETED]

  Expected line count: 152 - 27 (Architecture Conventions) + 4 (pointer replacement)
    - 23 (Gotchas section lines 126-149) - 2 (separator + comment) + 3 (new
    docs paths entries) = ~107 lines. Acceptable per architecture OQ-1 guidance
    (~105-110 is the realistic target after removing the two sections).
```

---

### Group D — Modify MEMORY.md (depends on: Group A complete)

MEMORY.md must not be modified until `docs/architecture.md`, `docs/gotchas.md`, and `docs/plans/index.md` exist, because the modification adds pointers to all three.

```
File: /Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md
Lines: 37-166 range affected; target ~100 lines total
Parallelism: depends-on: Group A (all three new docs created)
Description: Remove "Key Paths (Phase 1+)" section (lines 37-82, 46 lines),
  "Architecture Conventions" section (lines 84-110, 27 lines), and "Gotchas" section
  (lines 138-166, 29 lines). Add "Reference Docs" section after "Phase 1 Completion".
  Keep "Decisions & Overrides", "Session Cost History", "Completed This Session",
  "Next Session Work", "Phase 2 Backlog", and "Related Documentation Files" intact.
Details:
  DELETE: lines 37-82 ("## Key Paths (Phase 1+)" — all subsections:
    Shell Skills & Calibration, Python Package: Core Modules, Python Package: MCP
    Server, Tests, Documentation)
  DELETE: lines 84-110 ("## Architecture Conventions" — all subsections:
    Python Package Design, Session Recording & Calibration, Data Modules,
    Backward Compatibility, File Size Awareness)
  DELETE: lines 138-166 ("## Gotchas" — all subsections:
    Shell Scripting, Python Testing, API Design, Cascading Imports, MCP SDK Behavior)

  INSERT after line 35 ("Fix plan documented in project_ci_fix_plan.md..."),
  before "## Decisions & Overrides":

    ## Reference Docs

    - [docs/architecture.md](/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/architecture.md) — architecture decisions and coding conventions
    - [docs/gotchas.md](/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/gotchas.md) — known pitfalls and workarounds
    - [docs/plans/index.md](/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/plans/index.md) — plan file index

  Final structure of MEMORY.md (in order):
    1. H1 title
    2. ## Project Overview (unchanged)
    3. ## Phase 1 Completion (unchanged, lines 13-35)
    4. ## Reference Docs [NEW — 5 lines]
    5. ## Decisions & Overrides (Phase 1) (unchanged, lines 112-127 in original)
    6. ## Session Cost History (unchanged)
    7. ## Completed This Session (unchanged)
    8. ## Next Session Work (unchanged)
    9. ## Phase 2 Backlog (unchanged)
    10. ## Related Documentation Files (unchanged)

  Expected line count: 196 original
    - 46 (Key Paths section)
    - 27 (Architecture Conventions section)
    - 29 (Gotchas section)
    + 5 (Reference Docs section with blank lines)
    = ~99 lines. Under the 100-line target.

  IMPORTANT: The "Completed This Session" section (lines 168-173 in current file)
  and the "Next Session Work" section (lines 175-181) should be preserved verbatim.
  These are session-state records, not reference content.
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
  - Use standard file deletion (git rm or rm); this file is in the memory/ directory
    which is outside the git repo
  - Verify: grep -r "v1.2-plan" across all .md files to confirm no other doc references
    this file by name (the Related Documentation Files section in MEMORY.md does not
    reference it by name — it references project_ci_fix_plan.md and similar)
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
  - Group B: git mv 7 marketing files (single shell session)
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

1. **docs/architecture.md completeness checklist** — every bullet from CLAUDE.md lines 87-113 must appear in the new file. Count: 27 bullets in original. Verify all 27 are present by reading architecture.md and ticking off each CLAUDE.md bullet. Then verify every MEMORY.md Architecture section bullet (lines 84-110) is present. Any bullet missing from both sources is a content loss bug.

2. **docs/gotchas.md completeness checklist** — enumerate every bullet from:
   - CLAUDE.md Gotchas (lines 126-148): 4 subsections, ~17 bullets
   - MEMORY.md Gotchas (lines 138-166): 5 subsections, ~20 bullets
   Merged count should be ~30 unique bullets (7 duplicates collapsed). Verify all unique bullets present.

3. **Line count targets:**
   - `wc -l docs/architecture.md` — expect 80-100 lines
   - `wc -l docs/gotchas.md` — expect 70-90 lines
   - `wc -l docs/plans/index.md` — expect 45-60 lines

4. **docs/plans/index.md row count** — count rows in the table: should have 20 plan files (docs/plans/*.md) plus 1 superpowers file = 21 rows minimum.

### After Group B (marketing files moved)

5. **git status check:** `git status` should show exactly 7 renamed files (`docs/reddit-*.md` → `docs/marketing/reddit-*.md`, `docs/enterprise-strategy*.md` → `docs/marketing/enterprise-strategy*.md`). No unstaged deletions.

6. **No orphaned references:** `grep -r "reddit-feedback-analysis\|reddit-technical-response\|reddit-final-response\|enterprise-strategy" /Volumes/Macintosh\ HD2/Cowork/Projects/costscope --include="*.md" --include="*.py" --include="*.sh"` — any hits outside docs/marketing/ or docs/plans/index.md should be reviewed (none expected per architecture decision).

### After Group C (CLAUDE.md modified)

7. **Line count:** `wc -l /Volumes/Macintosh\ HD2/Cowork/Projects/costscope/CLAUDE.md` — expect 100-110 lines.

8. **Pointer present:** `grep -n "docs/architecture.md\|docs/gotchas.md" CLAUDE.md` — should return 2 lines in the Architecture & Conventions section.

9. **Deleted section gone:** `grep -n "## Architecture Conventions\|## Gotchas" CLAUDE.md` — should return 0 results.

10. **Docs update paths complete:** `grep -n "docs/architecture.md\|docs/gotchas.md\|docs/plans/index.md" CLAUDE.md` — should return hits in the Memory / Docs Update Paths section.

### After Group D (MEMORY.md modified)

11. **Line count:** `wc -l "/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md"` — expect 95-105 lines.

12. **Deleted sections gone:** `grep -n "## Key Paths\|## Architecture Conventions\|## Gotchas" MEMORY.md` — should return 0 results.

13. **Reference Docs section present:** `grep -n "## Reference Docs" MEMORY.md` — should return 1 hit.

14. **Preserved sections intact:** Verify "## Decisions & Overrides", "## Session Cost History", "## Completed This Session", "## Next Session Work", "## Phase 2 Backlog", "## Related Documentation Files" all still present.

### After Group E (memory files deleted)

15. **Files gone:** `ls "/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/"` — v1.2-plan.md and v1.4-plan.md should not appear.

### Final cross-check

16. **No cross-references broken:** Search for any remaining references to the deleted memory files: `grep -r "v1.2-plan\|v1.4-plan" /Volumes/Macintosh\ HD2/Cowork/Projects/costscope --include="*.md"` — expect 0 hits (these files were only referenced by their own content, not from other docs).

17. **No test files modified:** `git diff --name-only | grep "tests/"` — expect empty. This is a docs-only change.

18. **No source files modified:** `git diff --name-only | grep "^src/"` — expect empty.

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

4. **MEMORY.md "Completed This Session" and "Next Session Work" sections preserved verbatim**: These sections were added after the architecture was written (lines 168-181 in current file). They are session-state records and fall outside the scope of the restructure. Deleting them would lose operational state about the Pipeline Enforcement Hooks completion and pending CI fix work.

---

## Rollback Notes

This change is documentation-only with no code side effects.

**To rollback Group A (new files created):** Delete `docs/architecture.md`, `docs/gotchas.md`, and `docs/plans/index.md`. These are new files with no prior state.

**To rollback Group B (marketing files moved):** Run reverse git mv commands:
```bash
git mv docs/marketing/reddit-feedback-analysis.md docs/reddit-feedback-analysis.md
# ... repeat for all 7 files
rmdir docs/marketing/
```

**To rollback Group C (CLAUDE.md modified):** `git checkout CLAUDE.md` restores the original. No data is lost — the original Architecture Conventions and Gotchas sections are simply restored.

**To rollback Group D (MEMORY.md modified):** Restore from git is not possible (MEMORY.md is outside the repo). However, the content removed from MEMORY.md is fully preserved in `docs/architecture.md` and `docs/gotchas.md`. If MEMORY.md rollback is needed, the sections can be manually re-inserted from those files.

**To rollback Group E (memory files deleted):** The content of v1.2-plan.md and v1.4-plan.md is preserved in the `references/` directory per architecture RQ-4. The files themselves cannot be un-deleted without a backup, but their unique content is already captured. Before running Group E, the implementer should confirm this by reading `references/calibration-algorithm.md` lines 88-94 and 120-125.

**Recommended sequence for safer rollback:** Commit Group B (git mv) and Group A (new files) as separate commits from Group C+D (CLAUDE.md and MEMORY.md modifications). This allows reverting CLAUDE.md/MEMORY.md changes without losing the new reference docs.
