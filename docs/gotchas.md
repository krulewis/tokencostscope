# Gotchas

Update this file when new gotchas are discovered or existing ones are resolved. Remove entries when the underlying issue is fixed.

## Shell & File Paths

- **Paths with spaces**: Always quote shell paths. If the repo lives at a path with spaces (e.g., `/Volumes/My Drive/...`), unquoted shell commands will break. Use `-print0 | xargs -0` for `find` pipelines.
- **GNU vs BSD xargs**: Never use `find | xargs -0 ls -t` — GNU xargs (Linux) runs `ls` with no arguments on empty stdin, listing cwd. Use `find -exec ls -t {} +` instead, which only runs `ls` when files are found. Fixed in PR #14.
- **Worktree working directory**: If using git worktrees, the working dir differs from the main repo root. Use absolute paths.
- **README.md location**: `README.md` is in the repo root, not inside `.claude/skills/tokencast/`.
- **`calibration/` is gitignored**: Do not commit calibration data. The directory may not exist on a fresh clone; scripts must handle its absence gracefully.
- **macOS `timeout` command**: Not available by default. Tests use `fake_home` + HOME override instead of stdin.
- **midcheck.sh JSONL discovery**: Use `active-estimate.json` mtime as `-newer` reference, not directory mtime (which changes on `mkdir -p`). Wrap discovery in `if [ -f "$ESTIMATE_FILE" ]`.
- **Enforcement hooks**: All hooks in `.claude/hooks/` check `TOKENCAST_SKIP_GATE=1` first and exit 0 if set. `inline-edit-guard.sh` suppresses warnings when `agent_type` is present in the hook envelope (sub-agent context). `branch-guard.sh` uses `|| true` around `git branch --show-current` to fail-open in detached HEAD state. `validate-agent-type.sh` has no `set -e` — python3 failures produce `AGENT_TYPE=""` which exits 0 (fail-open). `estimate-gate.sh` accepts `CALIBRATION_DIR` and `TOKENCAST_SIZE_MARKER` env overrides for test isolation.

## Python Testing

- **Python versions**: The main test suite requires Python 3.9+. MCP tests require 3.10+. On macOS, check which `python3` you have — Homebrew may install a version without pytest. Use `python3 -m pytest` for the main test suite.
- **MCP package requirement**: `mcp >= 3.10`. Tests requiring MCP skip cleanly under 3.9 via `pytest.importorskip("mcp")` on Python 3.9.
- **test_mcp_scaffold.py runs under 3.10+ only**: `python3 -m pytest tests/test_mcp_scaffold.py` — requires Python 3.10+ with the `mcp` package installed.
- **sys.path.insert pattern**: Tests use `sys.path.insert(0, str(Path(__file__).parent.parent / "src"))` to import `tokencast` without requiring editable install. Must be placed BEFORE `pytest.importorskip("mcp")` so `tokencast_mcp` is found when running under Python 3.11.
- **Do NOT `import tokencast_mcp` in fast tests**: `tokencast_mcp/__init__.py` re-exports `run` from `tokencast_mcp.server`, which executes `from mcp.server import Server` at import time. In environments without the `mcp` package this raises `ModuleNotFoundError`. For version-string checks and other non-MCP assertions, read `src/tokencast_mcp/__init__.py` as a file and extract with regex instead of importing. See `tests/test_version_consistency.py` for the pattern.

## Python Package & Imports

- **importlib pattern for loading scripts**: `sum-session-tokens.py` and `learn.sh` use importlib to load Python modules (`pricing.py`, `session_recorder.py`) directly from `src/tokencast/`, bypassing `__init__.py`. This avoids pulling the full dependency tree in subprocess contexts. `api.py` does NOT use importlib — it imports the package modules directly (`from tokencast import calibration_store`, etc.).
- **Eager `__init__.py` is fine**: The cascading imports hypothesis (CI failures caused by `__init__.py` pulling MCP deps) was disproven. The real CI issue was GNU xargs (see Shell & File Paths). The eager imports in `__init__.py` are not a problem because learn.sh/sum-session-tokens.py use importlib to load modules directly.
- **Scripts not in wheel**: `scripts/` is NOT included in the built wheel. Any code that uses `Path(__file__).parent.parent.parent / "scripts"` to locate Python modules will crash at import time for `uvx`/`pip` installs. Fix: use package modules in `src/tokencast/` instead. The four affected scripts are now mirrored as `calibration_store.py`, `parse_last_estimate.py`, `tokencast_status.py`, and `update_factors.py` in `src/tokencast/`.
- **`tokencast_status.py` `heuristics_path=None` behavior**: When `heuristics_path=None` is passed to `build_status_output()`, `rec_stale_pricing()` returns `None` immediately (no stale pricing recommendation). `parse_review_cycles_default()` returns the default (2) via `except (OSError, TypeError)`. This is intentional — callers that don't have a heuristics file path get graceful degradation, not a crash.

## MCP SDK Behavior

- **`isError` always False from call_tool**: The server's `call_tool` handler catches `ValueError` and returns `TextContent` with error text — `isError` is always `False` (the SDK does not convert caught exceptions to `isError=True`). Check error text in `ctr.content[0].text` rather than asserting `isError`.
- **list_tools return type**: `list[Tool]` (not `ListToolsResult`).
- **MCP requires Python >= 3.10**: `mcp` package cannot be installed on Python 3.9. See Python Testing section for version requirements.

## Telemetry

- **`TOKENCAST_TELEMETRY_URL` removed**: The env var is no longer used. The PostHog endpoint (`https://us.i.posthog.com/capture/`) is hardcoded. Setting `TOKENCAST_TELEMETRY_URL` has no effect.
- **Install ID persistence**: `~/.tokencast/install_id` is created on first telemetry-enabled run. Atomic write via `os.rename()` handles concurrent MCP server starts. Empty or non-UUID4 content triggers regeneration.
- **`send_metrics` signature change**: `endpoint_url` parameter removed. Tests that pass `endpoint_url=` to `send_metrics` will get absorbed by `**_ignored` but should be updated.
- **PostHog API key placeholder**: `phc_PLACEHOLDER` in `telemetry.py` must be replaced with a real key before events reach PostHog. Events sent with the placeholder key are silently accepted but attributed to a nonexistent project.

## API Design

- **estimate_cost does NOT write active-estimate.json**: The MCP tool handler writes it. E2E tests use `_make_active_estimate()` helper.
- **report_session stub removal gotcha**: The old stub returned `{"recorded": False, "_stub": True}`. The real handler must NOT return `_stub` key. Tests check `"_stub" not in result`.
- **`estimate_cost` returns $0.00 for agent alias names**: `_resolve_steps()` in `estimation_engine.py` checks raw strings against `PIPELINE_STEPS` keys without resolving aliases. Passing `"qa"` instead of `"QA"`, or `"implementer"` instead of `"Implementation"`, silently drops all steps → $0.00. Fix: call `resolve_step_name()` before the membership check.
- **`build_status_output` signature**: `build_status_output(all_records, factors, verbose=False, window_spec=None, heuristics_path=None)`. Windowing is computed internally.
- **step_actuals schema**: Values are plain floats (cost in $), not dicts with `'actual'`/`'estimated'` sub-keys. Iteration: `for step_name, step_cost in r['step_actuals'].items()`.
- **`ServerConfig.ensure_dirs()`**: Directory creation is separated from config construction. `from_args()` does NOT create dirs — `ensure_dirs()` is called at server startup.

## Claude Code Plugin System

- **`${CLAUDE_PLUGIN_ROOT}` expansion**: The variable is used in `plugin/hooks/hooks.json` and resolves to the installed plugin directory (verified via SC-R12 live install test, 2026-03-30). Hooks load correctly after `/plugin install tokencast@tokencast` + `/reload-plugins`.
- **Plugin hook scripts vs repo hooks**: `plugin/hooks/` scripts (`tokencast-learn.sh`, `tokencast-midcheck.sh`, `tokencast-agent-hook.sh`) adapt the originals in `scripts/` with `${HOME}/.tokencast/calibration` as `CALIBRATION_DIR` instead of repo-relative paths. Do not modify the originals — keep both independent.
- **`session_recorder.py` is stdlib-only**: The plugin copies this file directly into `plugin/scripts/` and loads it without the tokencast package environment. Do not add non-stdlib imports to this file (json, pathlib, argparse are fine; tokencast.* imports are not).
- **`pricing.py` drift detection**: `plugin/scripts/pricing.py` is a verbatim copy of `src/tokencast/pricing.py`. The test `test_pricing_py_no_drift` in `tests/test_plugin_integrity.py` will catch drift on every CI run — do not manually edit the plugin copy.

## CI & Continuous Integration

- **CI is green** (as of 2026-03-27): 0 failures across Python 3.10, 3.11, 3.12 on ubuntu-latest. Fixed in PR #13 (test assertions) + PR #14 (GNU xargs compat).
- **bash -x tracing in learn.sh tests**: `_run_learn_sh` uses `bash -x` and includes the trace in assertion failure messages. If learn.sh integration tests fail on CI, the error message shows the full execution trace.
- **REPO_ROOT portability**: `Path(__file__).resolve().parent.parent.parent` must be used consistently; never use relative paths.
- **sys.executable in subprocess**: Always use `sys.executable` not bare `python3` when spawning subprocesses from tests. Ensures the same Python version runs the subprocess.
