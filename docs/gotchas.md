# Gotchas

Update this file when new gotchas are discovered or existing ones are resolved. Remove entries when the underlying issue is fixed.

## Shell & File Paths

- **Paths with spaces**: Always quote shell paths; use `-print0 | xargs -0` for `find` pipelines. The repo lives at `/Volumes/Macintosh HD2/Cowork/Projects/costscope` — the space in "Macintosh HD2" will break unquoted shell commands.
- **macOS volume path**: `/Volumes/Macintosh HD2/...` is the working directory; scripts run from there will have the space in the absolute path.
- **Worktree working directory**: If using git worktrees, the working dir differs from the main repo root. Use absolute paths.
- **README.md location**: `README.md` is in the repo root (`/Volumes/Macintosh HD2/Cowork/Projects/costscope/README.md`), not inside `.claude/skills/tokencast/`.
- **`calibration/` is gitignored**: Do not commit calibration data. The directory may not exist on a fresh clone; scripts must handle its absence gracefully.
- **macOS `timeout` command**: Not available by default. Tests use `fake_home` + HOME override instead of stdin.
- **midcheck.sh JSONL discovery**: Use `active-estimate.json` mtime as `-newer` reference, not directory mtime (which changes on `mkdir -p`). Wrap discovery in `if [ -f "$ESTIMATE_FILE" ]`.
- **Enforcement hooks**: All hooks in `.claude/hooks/` check `TOKENCAST_SKIP_GATE=1` first and exit 0 if set. `inline-edit-guard.sh` suppresses warnings when `agent_type` is present in the hook envelope (sub-agent context). `branch-guard.sh` uses `|| true` around `git branch --show-current` to fail-open in detached HEAD state. `validate-agent-type.sh` has no `set -e` — python3 failures produce `AGENT_TYPE=""` which exits 0 (fail-open). `estimate-gate.sh` accepts `CALIBRATION_DIR` and `TOKENCAST_SIZE_MARKER` env overrides for test isolation.

## Python Testing

- **Python versions**: `/usr/bin/python3` is 3.9.6 (has pytest). Homebrew `python3` is 3.14 (no pytest). Always use `/usr/bin/python3 -m pytest` for the main test suite.
- **MCP package requirement**: `mcp >= 3.10`. Tests requiring MCP skip cleanly under 3.9 via `pytest.importorskip("mcp")` on Python 3.9.
- **test_mcp_scaffold.py runs under 3.11 only**: `python3.11 -m pytest tests/test_mcp_scaffold.py` — do NOT try to run under `/usr/bin/python3` (3.9).
- **sys.path.insert pattern**: Tests use `sys.path.insert(0, str(Path(__file__).parent.parent / "src"))` to import `tokencast` without requiring editable install. Must be placed BEFORE `pytest.importorskip("mcp")` so `tokencast_mcp` is found when running under Python 3.11.

## Python Package & Imports

- **Cascading imports issue (in progress)**: `tokencast/__init__.py` currently imports everything at module level, triggering the entire MCP dependency tree. Subprocesses (learn.sh) that only need `session_recorder` get the full cascade. Fix: lazy `__getattr__`-based loading. After fix: revert importlib hacks in learn.sh and sum-session-tokens.py back to clean imports. NOTE: Remove this entry when lazy `__init__.py` lands.
- **importlib pattern for loading scripts**: `sum-session-tokens.py` and `learn.sh` use importlib to load Python modules from `scripts/` directory. This is a workaround for cascading imports (to be fixed with lazy `__getattr__`).

## MCP SDK Behavior

- **`isError` always False from call_tool**: The server's `call_tool` handler catches `ValueError` and returns `TextContent` with error text — `isError` is always `False` (the SDK does not convert caught exceptions to `isError=True`). Check error text in `ctr.content[0].text` rather than asserting `isError`.
- **list_tools return type**: `list[Tool]` (not `ListToolsResult`).
- **MCP requires Python >= 3.10**: `mcp` package cannot be installed on Python 3.9. See Python Testing section for version requirements.

## API Design

- **estimate_cost does NOT write active-estimate.json**: The MCP tool handler writes it. E2E tests use `_make_active_estimate()` helper.
- **report_session stub removal gotcha**: The old stub returned `{"recorded": False, "_stub": True}`. The real handler must NOT return `_stub` key. Tests check `"_stub" not in result`.
- **`build_status_output` signature**: `build_status_output(all_records, factors, verbose=False, window_spec=None, heuristics_path=None)`. Windowing is computed internally.
- **step_actuals schema**: Values are plain floats (cost in $), not dicts with `'actual'`/`'estimated'` sub-keys. Iteration: `for step_name, step_cost in r['step_actuals'].items()`.
- **`ServerConfig.ensure_dirs()`**: Directory creation is separated from config construction. `from_args()` does NOT create dirs — `ensure_dirs()` is called at server startup.

## CI & Continuous Integration

- **12 remaining CI failures** (as of 2026-03-27): All in `test_continuation_session.py::TestLearnShContinuation` × 4 tests × 3 Python versions. Root cause: cascading imports in `tokencast/__init__.py` prevent learn.sh subprocess from importing `session_recorder` alone. Fix documented in `project_ci_fix_plan.md`. NOTE: Remove when lazy `__init__.py` lands.
- **Error visibility in tests**: learn.sh uses `|| exit 0` and `2>/dev/null` everywhere. When CI fails, no diagnostic surfaces. Tests must capture stderr from `_run_learn_sh` helper and include it in assertion failures so CI shows the actual error.
- **REPO_ROOT portability**: `Path(__file__).resolve().parent.parent.parent` must be used consistently; never use relative paths.
- **sys.executable in subprocess**: Always use `sys.executable` not bare `python3` when spawning subprocesses from tests. Ensures the same Python version runs the subprocess.
