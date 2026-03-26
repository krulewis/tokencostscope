# US-1b.03: MCP Server Scaffold with stdio Transport — Implementation Plan

*Engineer Agent — Initial Plan*
*Date: 2026-03-26*

---

## Overview

This plan scaffolds the `tokencast_mcp` Python package inside `src/tokencast_mcp/`. The package exposes four MCP tool stubs (`estimate_cost`, `get_calibration_status`, `get_cost_history`, `report_session`) over stdio transport using the official MCP Python SDK. The stubs return placeholder responses; later stories (US-1b.04–1b.07) replace the stub bodies with real logic.

The key structural decisions already made by the architecture:
- Package lives at `src/tokencast_mcp/` (separate from the existing `src/tokencast/` skill package)
- `pyproject.toml` at repo root is extended — not a second pyproject.toml
- stdio transport only; no HTTP/SSE code
- `--calibration-dir` and `--project-dir` are parsed once at server startup and stored on a `ServerConfig` dataclass that all tool handlers can access via closure
- MCP SDK `@server.tool()` decorator registers tools with JSON Schema derived from type annotations
- Server logs to stderr; stdout is exclusively the MCP JSON-RPC stream

The MCP Python SDK (package `mcp`, version >= 1.0) exposes a `Server` class and `stdio_server()` async context manager. Tool handlers are `async def` functions decorated with `@server.tool()`. The SDK handles `initialize`, `tools/list`, and `tools/call` dispatch internally; callers only implement the handler bodies.

---

## Changes

### Change 1 — `src/tokencast_mcp/__init__.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/__init__.py
Lines: new file
Parallelism: independent
Estimated effort: 5 minutes
Description: Package marker. Exposes package version and re-exports the `run` entry-point function so `python -m tokencast_mcp` works.
```

Details:
- Set `__version__ = "0.1.0"` (matches existing `src/tokencast/__init__.py` version for initial release; will become 3.0.0 on the Phase 1b completion bump)
- Re-export `run` from `.server` so callers can do `from tokencast_mcp import run`
- No other imports at module level (keep import side-effects zero)

---

### Change 2 — `src/tokencast_mcp/__main__.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/__main__.py
Lines: new file
Parallelism: independent
Estimated effort: 5 minutes
Description: Enables `python -m tokencast_mcp`. Delegates entirely to `server.main()`.
```

Details:
- Single block: `from tokencast_mcp.server import main; main()`
- Wrapped in `if __name__ == "__main__":` guard
- No logic here — all argument parsing and startup is in `server.py`

---

### Change 3 — `src/tokencast_mcp/config.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/config.py
Lines: new file
Parallelism: independent
Estimated effort: 15 minutes
Description: ServerConfig dataclass that holds runtime configuration resolved from CLI arguments. Shared by all tool handlers via closure. Centralises path resolution logic.
```

Details:
- `from dataclasses import dataclass, field`
- `from pathlib import Path`
- `@dataclass` class `ServerConfig`:
  - `calibration_dir: Path` — absolute path to calibration directory; defaults to `Path.home() / ".tokencast" / "calibration"` if not provided via `--calibration-dir`
  - `project_dir: Path | None` — absolute path to project root for `wc -l` resolution; `None` if not provided
  - `history_path: Path` — derived property: `calibration_dir / "history.jsonl"`
  - `factors_path: Path` — derived property: `calibration_dir / "factors.json"`
  - `active_estimate_path: Path` — derived property: `calibration_dir / "active-estimate.json"`
  - `last_estimate_path: Path` — derived property: `calibration_dir / "last-estimate.md"`
- `@classmethod from_args(cls, calibration_dir: str | None, project_dir: str | None) -> "ServerConfig"`:
  - Resolves paths to absolute using `Path(...).expanduser().resolve()`
  - Creates `calibration_dir` on disk if it does not exist (`mkdir(parents=True, exist_ok=True)`)
- No I/O at import time

---

### Change 4 — `src/tokencast_mcp/tools/__init__.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/tools/__init__.py
Lines: new file
Parallelism: independent
Estimated effort: 2 minutes
Description: Empty package marker for the tools sub-package.
```

Details:
- Empty file (just a docstring: `"""Tool handler stubs for tokencast MCP server."""`)

---

### Change 5 — `src/tokencast_mcp/tools/estimate_cost.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/tools/estimate_cost.py
Lines: new file
Parallelism: independent
Estimated effort: 30 minutes
Description: Stub handler for the estimate_cost tool. Returns a placeholder response with the correct output schema shape. Real implementation added in US-1b.04.
```

Details:
- `from typing import Optional` and `from tokencast_mcp.config import ServerConfig`
- Public function: `async def handle_estimate_cost(params: dict, config: ServerConfig) -> dict`
  - Validates required keys: `size`, `files`, `complexity` — raises `ValueError` with a descriptive message if any are missing or have invalid values (`size` must be one of `XS|S|M|L`; `complexity` must be `low|medium|high`; `files` must be a non-negative int)
  - Returns a stub dict with the correct top-level shape:
    ```python
    {
        "version": "0.1.0",
        "estimate": {"optimistic": 0.0, "expected": 0.0, "pessimistic": 0.0},
        "steps": [],
        "metadata": {
            "size": params["size"],
            "files": params["files"],
            "complexity": params["complexity"],
            "file_brackets": None,
            "parallel_groups": params.get("parallel_groups", []),
            "pricing_last_updated": "unknown",
            "pricing_stale": False,
        },
        "_stub": True,
    }
    ```
  - Logs `[estimate_cost] stub called with size={size}, files={files}` to stderr
- Input schema constant `ESTIMATE_COST_SCHEMA: dict` — JSON Schema object that the server registers:
  ```python
  {
      "type": "object",
      "required": ["size", "files", "complexity"],
      "properties": {
          "size": {"type": "string", "enum": ["XS", "S", "M", "L"]},
          "files": {"type": "integer", "minimum": 0},
          "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
          "steps": {"type": "array", "items": {"type": "string"}},
          "project_type": {"type": "string"},
          "language": {"type": "string"},
          "review_cycles": {"type": "integer", "minimum": 0},
          "avg_file_lines": {"type": ["integer", "null"]},
          "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
          "file_paths": {"type": "array", "items": {"type": "string"}},
      },
      "additionalProperties": False,
  }
  ```

---

### Change 6 — `src/tokencast_mcp/tools/get_calibration_status.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/tools/get_calibration_status.py
Lines: new file
Parallelism: independent
Estimated effort: 20 minutes
Description: Stub handler for get_calibration_status. Returns a placeholder status dict. Real implementation (importlib delegation to tokencast-status.py) added in US-1b.05.
```

Details:
- Public function: `async def handle_get_calibration_status(params: dict, config: ServerConfig) -> dict`
  - Accepts optional `window` string (e.g., `"30d"`); default `"30d"`
  - Returns stub dict:
    ```python
    {
        "schema_version": 1,
        "health": {"status": "no_data", "message": "Stub — not yet implemented"},
        "_stub": True,
    }
    ```
  - Logs `[get_calibration_status] stub called` to stderr
- `GET_CALIBRATION_STATUS_SCHEMA: dict`:
  ```python
  {
      "type": "object",
      "properties": {
          "window": {"type": "string"},
      },
      "additionalProperties": False,
  }
  ```

---

### Change 7 — `src/tokencast_mcp/tools/get_cost_history.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/tools/get_cost_history.py
Lines: new file
Parallelism: independent
Estimated effort: 20 minutes
Description: Stub handler for get_cost_history. Returns empty history. Real implementation added in US-1b.06.
```

Details:
- Public function: `async def handle_get_cost_history(params: dict, config: ServerConfig) -> dict`
  - Accepts optional `window` (default `"30d"`) and `include_outliers` (default `False`)
  - Returns stub dict:
    ```python
    {
        "records": [],
        "summary": {
            "session_count": 0,
            "mean_ratio": None,
            "median_ratio": None,
            "pct_within_expected": None,
        },
        "_stub": True,
    }
    ```
  - Logs `[get_cost_history] stub called` to stderr
- `GET_COST_HISTORY_SCHEMA: dict`:
  ```python
  {
      "type": "object",
      "properties": {
          "window": {"type": "string"},
          "include_outliers": {"type": "boolean"},
      },
      "additionalProperties": False,
  }
  ```

---

### Change 8 — `src/tokencast_mcp/tools/report_session.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/tools/report_session.py
Lines: new file
Parallelism: independent
Estimated effort: 20 minutes
Description: Stub handler for report_session. Returns a not-yet-implemented message. Real implementation added in US-1b.07.
```

Details:
- Public function: `async def handle_report_session(params: dict, config: ServerConfig) -> dict`
  - Validates required key `actual_cost` — raises `ValueError` if missing or not a non-negative number
  - Returns stub dict:
    ```python
    {
        "recorded": False,
        "message": "Stub — report_session not yet implemented",
        "_stub": True,
    }
    ```
  - Logs `[report_session] stub called with actual_cost={actual_cost}` to stderr
- `REPORT_SESSION_SCHEMA: dict`:
  ```python
  {
      "type": "object",
      "required": ["actual_cost"],
      "properties": {
          "actual_cost": {"type": "number", "minimum": 0},
          "step_actuals": {
              "type": "object",
              "additionalProperties": {"type": "number"},
          },
          "turn_count": {"type": "integer", "minimum": 0},
          "review_cycles_actual": {"type": "integer", "minimum": 0},
      },
      "additionalProperties": False,
  }
  ```

---

### Change 9 — `src/tokencast_mcp/server.py`  (core — depends on Changes 3–8)

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/src/tokencast_mcp/server.py
Lines: new file
Parallelism: depends-on: Changes 3, 4, 5, 6, 7, 8
Estimated effort: 2–3 hours
Description: Main server module. Parses CLI args, builds ServerConfig, creates the MCP Server instance, registers all four tools, and runs the stdio event loop.
```

Details:

**Imports:**
```python
import argparse
import asyncio
import logging
import sys
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult, ListToolsResult
from tokencast_mcp.config import ServerConfig
from tokencast_mcp.tools.estimate_cost import handle_estimate_cost, ESTIMATE_COST_SCHEMA
from tokencast_mcp.tools.get_calibration_status import handle_get_calibration_status, GET_CALIBRATION_STATUS_SCHEMA
from tokencast_mcp.tools.get_cost_history import handle_get_cost_history, GET_COST_HISTORY_SCHEMA
from tokencast_mcp.tools.report_session import handle_report_session, REPORT_SESSION_SCHEMA
```

**Logging setup:**
```python
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="[tokencast-mcp] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
```
All log output goes to stderr. stdout is exclusively MCP JSON-RPC.

**`build_server(config: ServerConfig) -> Server` function:**
- Creates `server = Server("tokencast-mcp")`
- Registers `tools/list` handler via `@server.list_tools()`:
  - Returns `ListToolsResult` containing four `Tool` instances (name, description, `inputSchema` from the schema constants)
  - Tool descriptions:
    - `estimate_cost`: "Estimate Anthropic API token costs for a development plan before execution"
    - `get_calibration_status`: "Get calibration health and accuracy metrics for cost estimates"
    - `get_cost_history`: "Query historical cost estimation records and actuals"
    - `report_session`: "Report actual session cost to improve future calibration"
- Registers `tools/call` handler via `@server.call_tool()`:
  - `async def handle_call_tool(name: str, arguments: dict | None) -> CallToolResult`
  - `arguments` defaults to `{}` if `None`
  - Dispatch table (dict lookup, not if/elif chain):
    ```python
    _DISPATCH = {
        "estimate_cost": handle_estimate_cost,
        "get_calibration_status": handle_get_calibration_status,
        "get_cost_history": handle_get_cost_history,
        "report_session": handle_report_session,
    }
    ```
  - If `name` not in `_DISPATCH`: raise `ValueError(f"Unknown tool: {name!r}")` — the MCP SDK converts `ValueError` to a proper JSON-RPC error response
  - Calls handler with `(arguments, config)`, `await`-ing the result
  - Wraps result dict as `json.dumps(result)` in a `TextContent(type="text", text=...)` and returns `CallToolResult(content=[...])`
  - Catches `ValueError` from handlers and returns `CallToolResult(content=[TextContent(type="text", text=f"Error: {e}")], isError=True)`
  - Catches broad `Exception` for unexpected errors, logs to stderr, returns `isError=True` response
- Returns `server`

**`async def run_server(config: ServerConfig) -> None` function:**
- Calls `build_server(config)` to get the server instance
- Uses `async with stdio_server() as (read_stream, write_stream):`
- Calls `await server.run(read_stream, write_stream, server.create_initialization_options())`
- Logs startup info to stderr before entering the loop: `logger.info("tokencast-mcp started, calibration_dir=%s", config.calibration_dir)`

**`def parse_args(argv=None) -> argparse.Namespace` function:**
- `parser = argparse.ArgumentParser(prog="tokencast-mcp", description="tokencast MCP server")`
- `--calibration-dir`: optional str, help="Path to calibration directory (default: ~/.tokencast/calibration)"
- `--project-dir`: optional str, help="Project root for file measurement resolution"
- `--version`: action="version", version="%(prog)s 0.1.0"
- Returns `parser.parse_args(argv)`

**`def main(argv=None) -> None` function:**
- Calls `parse_args(argv)` → `args`
- Builds `config = ServerConfig.from_args(args.calibration_dir, args.project_dir)`
- Calls `asyncio.run(run_server(config))`
- Wraps in `try/except KeyboardInterrupt: pass` (clean Ctrl-C exit)

---

### Change 10 — `pyproject.toml` (extend existing file)

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/pyproject.toml
Lines: 1–37 (full file — extend existing content)
Parallelism: independent
Estimated effort: 15 minutes
Description: Add tokencast_mcp package to the wheel build target, add mcp>=1.0 dependency, add the tokencast-mcp console script entry point, and update the packages list.
```

Current file ends at line 37. Required additions:

1. Add `[project.dependencies]` table (currently absent):
   ```toml
   [project.dependencies]
   mcp = ">=1.0"
   ```

2. Update `[tool.hatch.build.targets.wheel]` `packages` list to include both packages:
   ```toml
   [tool.hatch.build.targets.wheel]
   packages = ["src/tokencast", "src/tokencast_mcp"]
   ```

3. Add `[project.scripts]` table for the `tokencast-mcp` CLI entry point:
   ```toml
   [project.scripts]
   tokencast-mcp = "tokencast_mcp.server:main"
   ```

No other fields change. The existing `name`, `version`, `description`, `authors`, `urls`, etc. are unchanged.

---

### Change 11 — `tests/test_mcp_scaffold.py`

```
File: /Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_mcp_scaffold.py
Lines: new file
Parallelism: depends-on: Changes 3, 4, 5, 6, 7, 8, 9
Estimated effort: 2–3 hours
Description: Protocol smoke tests and unit tests for the scaffold. Covers initialize, tools/list, tools/call (stub responses), error paths, and config resolution.
```

Details — test class structure:

**`TestServerConfig`:**
- `test_default_calibration_dir_is_absolute` — `ServerConfig.from_args(None, None)` produces an absolute `calibration_dir`
- `test_calibration_dir_override` — `from_args("/tmp/test-cal", None)` sets `calibration_dir` to `/tmp/test-cal`
- `test_project_dir_none_when_not_provided` — `project_dir` is `None` by default
- `test_derived_paths` — `history_path`, `factors_path`, `active_estimate_path`, `last_estimate_path` are all children of `calibration_dir`
- `test_calibration_dir_created_on_init` — `from_args` with a non-existent path creates the directory

**`TestToolSchemas`:**
- `test_estimate_cost_schema_required_fields` — `ESTIMATE_COST_SCHEMA["required"]` contains `["size", "files", "complexity"]`
- `test_report_session_schema_required_fields` — `REPORT_SESSION_SCHEMA["required"]` contains `["actual_cost"]`
- `test_all_tools_have_schemas` — import all four schema constants; verify each is a dict with `"type": "object"`

**`TestToolStubs`** (async tests using `pytest-asyncio` or `asyncio.run`):
- `test_estimate_cost_stub_returns_correct_shape` — call `handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config)`, assert keys `version`, `estimate`, `steps`, `metadata`, `_stub` present
- `test_estimate_cost_missing_size_raises_value_error` — `handle_estimate_cost({"files": 5, "complexity": "medium"}, config)` raises `ValueError`
- `test_estimate_cost_invalid_size_raises_value_error` — `size="XL"` (not in enum) raises `ValueError`
- `test_get_calibration_status_stub_returns_schema_version` — response has `schema_version == 1`
- `test_get_cost_history_stub_returns_empty_records` — response `records` is `[]`
- `test_report_session_stub_missing_actual_cost_raises` — `handle_report_session({}, config)` raises `ValueError`
- `test_report_session_stub_valid_input_returns_recorded_false` — `handle_report_session({"actual_cost": 1.5}, config)` returns `{"recorded": False, ...}`

**`TestServerBuildAndDispatch`** (in-process, no subprocess):
- `test_build_server_returns_server_instance` — `build_server(config)` returns an `mcp.server.Server` instance
- `test_unknown_tool_raises_value_error` — manually call the registered `call_tool` handler with `name="nonexistent_tool"`, assert `ValueError` raised or `isError=True` in result
- `test_tools_list_returns_four_tools` — invoke the `list_tools` handler, assert result contains exactly 4 tools with names matching `{"estimate_cost", "get_calibration_status", "get_cost_history", "report_session"}`

**`TestProtocolSmoke`** (subprocess-based, tagged `@pytest.mark.slow`):
- `test_server_starts_and_responds_to_initialize` — spawn `python -m tokencast_mcp --calibration-dir <tmpdir>` as a subprocess, send a JSON-RPC `initialize` message over stdin, read the response from stdout, assert valid JSON-RPC response with `result.protocolVersion` present
- `test_tools_list_via_stdio` — extend the subprocess test: after initialize, send `{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}`, assert 4 tools in `result.tools`
- `test_call_unknown_tool_returns_error` — send `tools/call` with `name="bogus"`, assert the response has `error` (JSON-RPC error) or `isError=True`

**Note on async tests:** Use `asyncio.run()` in a synchronous test wrapper rather than `pytest-asyncio` to avoid adding a new test dependency. Pattern:
```python
import asyncio

def test_something():
    result = asyncio.run(handle_estimate_cost({...}, config))
    assert ...
```

---

## Dependency Order

The following serialization order must be respected:

1. **Parallel group A (no dependencies — create simultaneously):**
   - Change 1: `src/tokencast_mcp/__init__.py`
   - Change 2: `src/tokencast_mcp/__main__.py`
   - Change 3: `src/tokencast_mcp/config.py`
   - Change 4: `src/tokencast_mcp/tools/__init__.py`
   - Change 5: `src/tokencast_mcp/tools/estimate_cost.py`
   - Change 6: `src/tokencast_mcp/tools/get_calibration_status.py`
   - Change 7: `src/tokencast_mcp/tools/get_cost_history.py`
   - Change 8: `src/tokencast_mcp/tools/report_session.py`
   - Change 10: `pyproject.toml` updates

2. **Sequential after group A completes:**
   - Change 9: `src/tokencast_mcp/server.py` (imports Changes 3–8)

3. **Sequential after Change 9 completes:**
   - Change 11: `tests/test_mcp_scaffold.py` (tests Changes 3–9)

---

## Test Strategy

### Test file
`/Volumes/Macintosh HD2/Cowork/Projects/costscope/tests/test_mcp_scaffold.py`

### Run command
```bash
/usr/bin/python3 -m pytest tests/test_mcp_scaffold.py -v
/usr/bin/python3 -m pytest tests/test_mcp_scaffold.py -v -m "not slow"  # skip subprocess tests
```

### Happy path
- `estimate_cost` stub with valid `{size, files, complexity}` returns a dict with the correct shape and `_stub: True`
- `get_calibration_status` stub returns `schema_version: 1`
- `get_cost_history` stub returns empty `records: []`
- `report_session` stub with `actual_cost: 1.5` returns `recorded: False`
- `tools/list` returns exactly 4 tools

### Edge cases
- `estimate_cost` with `files: 0` (zero files) is valid — no error
- `estimate_cost` with `file_paths: []` (empty list) is valid
- `get_cost_history` with `include_outliers: true` — accepted without error (stub ignores it)
- `report_session` with `actual_cost: 0` — valid (zero-cost session)
- `--calibration-dir` pointing to a path with spaces (macOS volume path gotcha) — resolved correctly

### Error cases
- `estimate_cost` missing `size` → `ValueError` with message mentioning `size`
- `estimate_cost` `size="INVALID"` → `ValueError` mentioning allowed values
- `estimate_cost` `files=-1` → `ValueError` (negative file count)
- `report_session` missing `actual_cost` → `ValueError`
- `report_session` `actual_cost=-1` → `ValueError`
- Unknown tool name in `tools/call` → `isError=True` response (not a server crash)
- `tools/call` with `arguments: null` — treated as `{}`, no crash

### Existing tests that may be affected
The existing 441-test suite tests `scripts/` modules only. The new `src/tokencast_mcp/` package does not modify any existing `scripts/` file. All 441 tests should continue to pass unchanged. Verify with:
```bash
/usr/bin/python3 -m pytest tests/ -v --ignore=tests/test_mcp_scaffold.py
```

### Tests that can be written in parallel with implementation
`TestServerConfig` and `TestToolSchemas` can be written before `server.py` exists (they only test `config.py` and the schema constants). `TestServerBuildAndDispatch` and `TestProtocolSmoke` must wait for `server.py`.

---

## Rollback Notes

- All changes are additive: new files in `src/tokencast_mcp/` and additions to `pyproject.toml`
- No existing files are modified other than `pyproject.toml`
- To roll back: delete `src/tokencast_mcp/` and revert `pyproject.toml` to its current state (lines 35–37 only change)
- No database or calibration data is written during scaffolding (stubs do not touch the filesystem)
- No migration steps required — this is a net-new package alongside the existing one

---

## MCP SDK API Notes for Implementer

The MCP Python SDK (`mcp` package, >= 1.0) API relevant to this scaffold:

```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult, ListToolsResult

server = Server("tokencast-mcp")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="estimate_cost",
            description="...",
            inputSchema=ESTIMATE_COST_SCHEMA,
        ),
        # ... other tools
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    # dispatch and return list of content items
    ...

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
```

The `initialize` method and the protocol handshake are handled entirely by `server.run()` — implementers do not write an `initialize` handler.

The `@server.call_tool()` handler must return a list of content objects (e.g., `[TextContent(type="text", text=json.dumps(result))]`), not a bare dict. `CallToolResult` wraps this list.

For unknown tools, raise `ValueError` from within the handler — the SDK translates this to a JSON-RPC error response with code -32602 (Invalid params). Do not raise `McpError` manually; `ValueError` is sufficient for tool-level errors.

**Note:** If the exact SDK import paths differ (e.g., `mcp.server.models` instead of `mcp.types`), the implementer should run `python3 -c "import mcp; help(mcp)"` and adjust imports accordingly. The SDK has been stable since 1.0 but import paths occasionally shift between minor versions. Pin to `mcp>=1.0,<2.0` in `pyproject.toml` to avoid major-version breaks.
