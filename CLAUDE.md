# tokencast

A Claude Code skill that automatically estimates Anthropic API token costs when a development plan is created, and learns from actual usage over time to improve accuracy via calibration factors.

## Repo

- GitHub: `krulewis/tokencast`
- **SKILL.md version**: 2.1.0 (Claude Code skill)
- **PyPI package version**: 0.1.0 (independent versioning; Phase 1 implementation)

## Key Files

| Path | Purpose |
|------|---------|
| `SKILL.md` | Skill definition — activation rules, calculation algorithm, output template (v2.1.0) |
| `references/heuristics.md` | Token budgets, pipeline step decompositions, complexity multipliers, parallel discount parameters — all tunable parameters live here |
| `references/pricing.md` | Model pricing per million tokens, cache rates, step→model mapping |
| `references/calibration-algorithm.md` | Calibration algorithm documentation |
| `references/examples.md` | Worked estimation examples |
| `scripts/tokencast-learn.sh` | Stop hook — reads session JSONL, computes actuals, calls `update-factors.py` |
| `scripts/tokencast-midcheck.sh` | PreToolUse hook — warns if trending toward pessimistic band |
| `scripts/tokencast-agent-hook.sh` | PreToolUse+PostToolUse hook — writes sidecar timeline for per-agent step attribution |
| `scripts/update-factors.py` | Computes and persists calibration factors from session data |
| `scripts/sum-session-tokens.py` | Parses session JSONL, computes per-step actuals via sidecar |
| `scripts/calibration_store.py` | Storage abstraction for `history.jsonl` and `factors.json` |
| `scripts/tokencast-status.py` | Calibration health dashboard (v2.0) |
| `calibration/` | Calibration data — gitignored; contains `history.jsonl`, `factors.json`, `active-estimate.json` |
| `src/tokencast/__init__.py` | Package exports via lazy `__getattr__` (Phase 1 migration in progress) |
| `src/tokencast/api.py` | Public API layer — dict-based routing; `estimate_cost()`, `report_session()`, `report_step_cost()` |
| `src/tokencast/estimation_engine.py` | Core estimation: `compute_estimate(params, calibration_dir)` |
| `src/tokencast/file_measurement.py` | File size bracket measurement: `measure_files()`, `assign_bracket()` |
| `src/tokencast/pricing.py` | Cost computation: `compute_cost_from_usage(usage, model)` |
| `src/tokencast/heuristics.py` | Tunable parameters (derived from `references/heuristics.md`) |
| `src/tokencast/session_recorder.py` | `build_history_record()` — shared by shell and MCP paths |
| `src/tokencast/step_names.py` | Step name resolution: `resolve_step_name()` |
| `src/tokencast/telemetry.py` | Opt-in anonymous metrics |
| `src/tokencast/calibration_store.py` | Storage abstraction |
| `src/tokencast_mcp/server.py` | MCP server: `main()`, `build_server()`, tool dispatcher |
| `src/tokencast_mcp/tools/` | MCP handlers: `estimate_cost`, `get_calibration_status`, `get_cost_history`, `report_step_cost`, `report_session` |
| `docs/wiki/` | GitHub wiki source — Home, How-It-Works, Installation, Configuration, Calibration, Roadmap, Attribution |
| `docs/attribution-protocol.md` | Framework-agnostic attribution protocol spec (v1) |
| `docs/phase-1-execution-plan.md` | Owner decisions and story inventory (Phase 1 completed) |
| `README.md` | Repo root README (PyPI package docs) |
| `pyproject.toml` | Package metadata; entry point `tokencast-mcp = "tokencast_mcp.server:main"` |

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

## Test Commands

```bash
# Run all tests — use system Python 3.9 which has pytest
/usr/bin/python3 -m pytest tests/

# Run a specific test file
/usr/bin/python3 -m pytest tests/test_pr_review_loop.py

# Run with verbose output
/usr/bin/python3 -m pytest tests/ -v

# Run MCP-dependent tests (requires Python >= 3.10)
python3.11 -m pytest tests/test_mcp_scaffold.py -v
```

**Do NOT use `pytest` or `python3 -m pytest` directly.** Homebrew `python3` resolves to 3.14 which does NOT have pytest. Always use `/usr/bin/python3` (3.9.6, has pytest) for the main test suite. Use `python3.11 -m pytest` for MCP-specific protocol tests.

**Test count**: 939 passing, 71 skipped (MCP-dependent tests requiring Python >= 3.10 are skipped cleanly under 3.9 via `pytest.importorskip("mcp")`).

## Architecture Conventions

- **All tunable parameters live in `references/heuristics.md`** — not hardcoded in SKILL.md. This includes complexity multipliers, band multipliers, parallel discount factors, cache rate floors, review cycle defaults, decay halflife, per-signature min samples, and midcheck parameters.
- **Time-decay constants:** `DECAY_HALFLIFE_DAYS = 30` in `update-factors.py` mirrors `decay_halflife_days` in `references/heuristics.md`. `DECAY_MIN_RECORDS = 5` (cold-start guard) is hardcoded in `update-factors.py` and intentionally NOT in heuristics.md — it is a statistical invariant, not user-tunable.
- **Per-signature factors:** Pass 5 of `update-factors.py` computes per-signature factors from signature-normalized step arrays. Signatures are derived at Pass 1 read time and stored as a private `_canonical_sig` field. In `factors.json`, they live under `signature_factors` and are read with `.get('signature_factors', {})` default for backward compatibility.
- **Mid-session check:** `tokencast-midcheck.sh` is a PreToolUse hook. It reads `active-estimate.json` and the session JSONL to compute actual spend, then writes state to `calibration/.midcheck-state` (ephemeral, gitignored). Hook is fail-silent via `set -euo pipefail` + `|| exit 0` — failures do not interrupt your work. State file format: two lines — last-checked byte size and cooldown sentinel (`0` or `COOLDOWN:<size>`).
- **Pipeline signature derivation:** Not written to `active-estimate.json`. SKILL.md Step 3e derives it inline from the `steps` array using the same normalization formula as `learn.sh` line 38.
- **Shell injection safety** — `learn.sh` and `midcheck.sh` use `shlex.quote()` and env vars pattern to pass data to Python. Never interpolate user-derived strings directly into shell commands.
- **`active-estimate.json` is the handshake** between estimation (SKILL.md writes it at estimate time) and learning (learn.sh reads it at session end). Schema changes must be backward compatible.
- **Backward compatibility** — new fields in `active-estimate.json` and `factors.json` schemas use `.get()` defaults in Python so old files don't break newer scripts.
- **File size brackets** — when file paths are extractable from the plan and files exist on disk, tokencast auto-measures via batched `wc -l` (cap: 30 files). Three brackets: small (≤49 lines) = 3k/1k tokens (read/edit), medium (50–500) = 10k/2.5k, large (≥501) = 20k/5k. Fixed-count file reads in all steps use the weighted-average bracket. Override: `avg_file_lines=N`. Unmeasured files fall back to override bracket or medium default.
- **`file_brackets` in active-estimate.json** — stores aggregate bracket counts (not per-file data) for future calibration stratification. Schema: `{"small": N, "medium": N, "large": N}` or null. `null` means no paths extracted (not the same as `{"small":0,"medium":0,"large":0}` which means paths extracted but none measurable).
- **Version string must be consistent** across three places: `SKILL.md` frontmatter (`version:`), output template header (`## tokencast estimate (v1.x.x)`), and `learn.sh` `VERSION` variable. Always update all three together.
- **PR Review Loop calibration** applies the factor independently to each band (not re-anchored as fixed ratios of calibrated Expected) — this preserves the decay model's per-band cycle counts.
- **Step 3.5 runs post-step-loop** — the PR Review Loop row computation happens after all individual pipeline steps complete Steps 3a–3e, not inline. Cache each constituent step's pre-discount cost during the per-step loop.
- **Parallel discount does NOT apply to PR Review Loop C value** — `C` uses undiscounted step costs even when constituent steps were modeled as parallel.
- **Attribution protocol (v3.x+)** — `docs/attribution-protocol.md` is the source of truth for the MCP attribution wire format. Version field is `attribution_protocol_version: 1`. Minor additions (new optional fields) do not require a version bump. Removing or renaming required fields does.
- **MCP tools are thin wrappers** — `src/tokencast_mcp/` exposes `estimate_cost`, `report_step_cost`, and `report_session` as MCP tools. Each delegates to the corresponding function in `src/tokencast/`. No business logic lives in the MCP layer.
- **Session recorder API is dict-based** — `build_history_record()` accepts an `attribution` parameter with source-specific fields (`step_actuals_mcp`, `step_actuals_sidecar`). All three attribution paths (`"mcp"`, `"sidecar"`, `"proportional"`) produce records with identical schema. The `attribution_method` field in history records distinguishes them.
- **Step-cost accumulator** — accumulated `report_step_cost` data is persisted to `calibration/{hash}-step-accumulator.json` after each call (atomic rename pattern). Cleared when `report_session` completes or when a new `estimate_cost` call is made. Hash is the first 12 chars of MD5 of the `active-estimate.json` absolute path — same hash used by `agent-hook.sh`.
- **Hook placement:** Enforcement hooks live in `.claude/hooks/` (not `scripts/`). Core tokencast functionality remains in `scripts/`. Enforcement hooks use `bash '/absolute/path/...'` in `settings.json` to match the existing hook pattern and handle the space in "Macintosh HD2".
- **`src/tokencast/` package exports** — `estimate_cost` and `report_session` must be importable from `tokencast/__init__.py` to support CI/CD usage without the MCP layer (`from tokencast import estimate_cost, report_session`).
- **Pricing module** — `src/tokencast/pricing.py` exposes `compute_cost_from_usage(usage: dict, model: str) -> float`. This is the framework-agnostic cost function. `compute_line_cost()` in `sum-session-tokens.py` extracts usage from Claude Code JSONL format and delegates to `compute_cost_from_usage()`.
- **Lazy `__init__.py` migration (Phase 1 fix)** — Replace eager imports with `__getattr__`-based lazy loading to prevent cascading imports in `import tokencast`. Preserves `from tokencast import estimate_cost` for end users but doesn't trigger full dependency tree on bare `import tokencast`. This fixes 12 remaining CI failures in `test_continuation_session.py`.
- **CI gotchas — REPO_ROOT portability**: `REPO_ROOT = Path(__file__).resolve().parent.parent.parent` must be used consistently across all Python modules to ensure paths work in both subprocess (learn.sh) and in-process (tests) contexts. Use absolute paths, never relative.
- **CI gotchas — `sys.executable` in subprocess**: Always use `sys.executable` instead of bare `python3` when spawning subprocesses from tests. Ensures the same Python version runs the subprocess. Tests may be under `/usr/bin/python3` (3.9) but need to spawn compatible interpreters.
- **CI gotchas — Error logging in learn.sh**: Shell scripts use `|| exit 0` and `2>/dev/null` everywhere. When tests fail, no diagnostic is visible. Next session's CI fix plan adds error logging: capture stderr from Python subprocesses and log before exiting; reduce `2>/dev/null` redirections so failures surface.

## Memory / Docs Update Paths

When completing work, the `docs-updater` agent should update:
- `docs/wiki/` — whichever wiki pages cover the changed functionality
- `MEMORY.md` at `/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md`
- `ROADMAP.md` if version or milestone status changed

## Project-Specific Estimate Overrides

- **`review_cycles=4`** — use this override when running `/tokencast` for tokencast changes. The global `heuristics.md` default of 2 is too low for this project; historical data across 5 sessions averages 4–5 passes (v1.3: 5, v1.5: 4, v1.6: 3, v1.7+v2.0: 4, v2.1: 11).

## Gotchas

### Shell & File Paths
- **Paths with spaces** — always quote shell paths; use `-print0 | xargs -0` for `find` pipelines. The repo lives at `/Volumes/Macintosh HD2/Cowork/Projects/costscope` — the space in "Macintosh HD2" will break unquoted shell commands.
- **macOS volume path** — `/Volumes/Macintosh HD2/...` is the working directory; scripts run from there will have the space in the absolute path.
- **Worktree working directory** — if using git worktrees, the working dir differs from the main repo root. Use absolute paths.
- **README.md location** — `README.md` is in the repo root (`/Volumes/Macintosh HD2/Cowork/Projects/costscope/README.md`), not inside `.claude/skills/tokencast/`.
- **`calibration/` is gitignored** — do not commit calibration data. The directory may not exist on a fresh clone; scripts must handle its absence gracefully.
- **Enforcement hooks:** All hooks in `.claude/hooks/` check `TOKENCAST_SKIP_GATE=1` first and exit 0 if set. `inline-edit-guard.sh` suppresses warnings when `agent_type` is present in the hook envelope (sub-agent context). `branch-guard.sh` uses `|| true` around `git branch --show-current` to fail-open in detached HEAD state. `validate-agent-type.sh` has no `set -e` — python3 failures produce `AGENT_TYPE=""` which exits 0 (fail-open). `estimate-gate.sh` accepts `CALIBRATION_DIR` and `TOKENCAST_SIZE_MARKER` env overrides for test isolation.

### Python Package & Imports (Phase 1+)
- **Cascading imports issue (in progress)**: `tokencast/__init__.py` currently imports everything at module level, triggering the entire MCP dependency tree. Subprocesses (learn.sh) that only need `session_recorder` get the full cascade. Fix in next session: lazy `__getattr__`-based loading.
- **sys.path.insert pattern for tests**: `sys.path.insert(0, str(Path(__file__).parent.parent / "src"))` used by all tests to import `tokencast` without requiring editable install. Must be placed BEFORE `pytest.importorskip("mcp")` so `tokencast_mcp` is found when running under Python 3.11.
- **importlib pattern for loading scripts**: `sum-session-tokens.py` and `learn.sh` use importlib to load Python modules from scripts/ directory. This is a workaround for cascading imports (to be fixed with lazy `__getattr__`).

### MCP & Testing
- **MCP requires Python >= 3.10**: `mcp` package does not install on Python 3.9. Tests using MCP skip cleanly via `pytest.importorskip("mcp")` on 3.9.
- **MCP protocol tests**: Run MCP-specific tests with `python3.11 -m pytest tests/test_mcp_scaffold.py` — do NOT try to run under `/usr/bin/python3` (3.9).
- **`isError` always False from call_tool**: The server's `call_tool` handler catches `ValueError` and returns `TextContent` with error text — `isError` is always `False` (the SDK does not convert caught exceptions to `isError=True`). Check error text in response instead.

### CI & Continuous Integration
- **12 remaining CI failures** (as of 2026-03-27): All in `test_continuation_session.py::TestLearnShContinuation` × 4 tests × 3 Python versions. Root cause: cascading imports in `tokencast/__init__.py` prevent learn.sh subprocess from importing `session_recorder` alone. Fix documented in `project_ci_fix_plan.md`.
- **Error visibility in tests**: learn.sh uses `|| exit 0` and `2>/dev/null` everywhere. When CI fails, no diagnostic surfaces. Tests must capture stderr from `_run_learn_sh` helper and include it in assertion failures so CI shows the actual error.

---

<!-- Global pipeline, workflow, agent delegation, and codebase-memory rules are in ~/.claude/CLAUDE.md — loaded automatically every session. No need to duplicate here. -->
