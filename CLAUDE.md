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

## Architecture & Conventions

See [docs/architecture.md](docs/architecture.md) for architecture decisions and coding conventions.
See [docs/gotchas.md](docs/gotchas.md) for known pitfalls and workarounds.

## Memory / Docs Update Paths

When completing work, the `docs-updater` agent should update:
- `docs/architecture.md` — if architecture decisions or coding conventions changed
- `docs/gotchas.md` — if new gotchas discovered or existing ones resolved
- `docs/plans/index.md` — if new plan files added to docs/plans/
- `docs/wiki/` — whichever wiki pages cover the changed functionality
- `MEMORY.md` at `/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md`
- `ROADMAP.md` if version or milestone status changed

## Project-Specific Estimate Overrides

- **`review_cycles=4`** — use this override when running `/tokencast` for tokencast changes. The global `heuristics.md` default of 2 is too low for this project; historical data across 5 sessions averages 4–5 passes (v1.3: 5, v1.5: 4, v1.6: 3, v1.7+v2.0: 4, v2.1: 11).

