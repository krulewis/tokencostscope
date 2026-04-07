# tokencast

A Claude Code skill that automatically estimates Anthropic API token costs when a development plan is created, and learns from actual usage over time to improve accuracy via calibration factors.

## Repo

- GitHub: `krulewis/tokencast`
- **PyPI package version**: 0.1.4
- **Distribution**: MCP server via `uvx tokencast` (registered in MCP Registry as `io.github.krulewis/tokencast`)
- **SKILL.md**: Retained as algorithm reference doc, but no longer installed as a Claude Code skill. MCP server is the primary interface.

## Key Files

| Path | Purpose |
|------|---------|
| `SKILL.md` | Algorithm reference doc — estimation formulas, output template (v2.1.0, no longer installed as skill) |
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
| `src/tokencast/__init__.py` | Package exports (eager imports — lazy migration was not needed; CI fix was GNU xargs compat) |
| `src/tokencast/api.py` | Public API layer — dict-based routing; `estimate_cost()`, `report_session()`, `report_step_cost()` |
| `src/tokencast/estimation_engine.py` | Core estimation: `compute_estimate(params, calibration_dir)` |
| `src/tokencast/file_measurement.py` | File size bracket measurement: `measure_files()`, `assign_bracket()` |
| `src/tokencast/pricing.py` | Cost computation: `compute_cost_from_usage(usage, model)` |
| `src/tokencast/heuristics.py` | Tunable parameters (derived from `references/heuristics.md`) |
| `src/tokencast/session_recorder.py` | `build_history_record()` — shared by shell and MCP paths |
| `src/tokencast/step_names.py` | Step name resolution: `resolve_step_name()` (handles alias mapping) |
| `src/tokencast/telemetry.py` | Opt-out PostHog telemetry (endpoint hardcoded, install ID at `~/.tokencast/install_id`, no-telemetry file at `~/.tokencast/no-telemetry`) |
| `src/tokencast/calibration_store.py` | Storage abstraction for history and factors |
| `src/tokencast/parse_last_estimate.py` | Reconstitution of minimal estimates from `last-estimate.md` (package module) |
| `src/tokencast/tokencast_status.py` | Calibration health dashboard utilities (package module) |
| `src/tokencast/update_factors.py` | Calibration factor computation and persistence (package module) |
| `src/tokencast_mcp/server.py` | MCP server: `main()`, `build_server()`, tool dispatcher |
| `src/tokencast_mcp/tools/` | MCP handlers: `estimate_cost`, `get_calibration_status`, `get_cost_history`, `report_step_cost`, `report_session`, `disable_telemetry` |
| `src/tokencast_mcp/tools/disable_telemetry.py` | Handler for `disable_telemetry` tool — creates `~/.tokencast/no-telemetry` file (v0.1.5+) |
| `docs/wiki/` | GitHub wiki source — Home, How-It-Works, Installation, Configuration, Calibration, Roadmap, Attribution |
| `docs/attribution-protocol.md` | Framework-agnostic attribution protocol spec (v1) |
| `docs/phase-1-execution-plan.md` | Owner decisions and story inventory (Phase 1 completed) |
| `README.md` | Repo root README (PyPI package docs) |
| `pyproject.toml` | Package metadata; entry point `tokencast-mcp = "tokencast_mcp.server:main"` |

## Hooks

Six workflow enforcement hooks live in `.claude/hooks/`. They enforce estimation gates, branch protection, and agent dispatch validation. Set `TOKENCAST_SKIP_GATE=1` to bypass all gates in emergencies.

## Test Commands

```bash
# Run all tests (requires Python 3.9+)
python3 -m pytest tests/

# Run MCP-dependent tests (requires Python >= 3.10)
python3 -m pytest tests/test_mcp_scaffold.py -v
```

**Python version notes:** The main test suite requires Python 3.9+. MCP-dependent tests require Python 3.10+ and skip cleanly on older versions via `pytest.importorskip("mcp")`.

**Test count**: 1094 passing, 93 skipped (0.1.6 adds 23 Test 3 criteria tests).

**CI status**: All green — 0 failures across Python 3.10, 3.11, 3.12 on ubuntu-latest.

## Architecture & Conventions

- [docs/architecture.md](docs/architecture.md) — architecture decisions and coding conventions
- [docs/gotchas.md](docs/gotchas.md) — known pitfalls and workarounds

## Docs Update Paths

When completing work, update:
- `docs/architecture.md` — if architecture decisions or coding conventions changed
- `docs/gotchas.md` — if new gotchas discovered or existing ones resolved
- `docs/plans/index.md` — if new plan files added to docs/plans/
- `docs/wiki/` — whichever wiki pages cover the changed functionality
- `ROADMAP.md` if version or milestone status changed

