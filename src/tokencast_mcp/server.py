"""tokencast MCP server — stdio transport.

Parses CLI args, builds ServerConfig, creates the MCP Server instance,
registers all five tools, and runs the stdio event loop.

Stdout is exclusively the MCP JSON-RPC stream. All log output goes to stderr.
"""

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from tokencast import telemetry as _telemetry
from tokencast_mcp import __version__
from tokencast_mcp.config import ServerConfig
from tokencast_mcp.tools.estimate_cost import (
    ESTIMATE_COST_SCHEMA,
    handle_estimate_cost,
)
from tokencast_mcp.tools.get_calibration_status import (
    GET_CALIBRATION_STATUS_SCHEMA,
    handle_get_calibration_status,
)
from tokencast_mcp.tools.get_cost_history import (
    GET_COST_HISTORY_SCHEMA,
    handle_get_cost_history,
)
from tokencast_mcp.tools.report_session import (
    REPORT_SESSION_SCHEMA,
    handle_report_session,
)
from tokencast_mcp.tools.report_step_cost import (
    REPORT_STEP_COST_SCHEMA,
    handle_report_step_cost,
)
from tokencast_mcp.tools.disable_telemetry import (
    DISABLE_TELEMETRY_SCHEMA,
    handle_disable_telemetry,
)

# ---------------------------------------------------------------------------
# Logging — stderr only; stdout is reserved for MCP JSON-RPC
# ---------------------------------------------------------------------------

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[tokencast-mcp] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_DISPATCH = {
    "estimate_cost": handle_estimate_cost,
    "get_calibration_status": handle_get_calibration_status,
    "get_cost_history": handle_get_cost_history,
    "report_session": handle_report_session,
    "report_step_cost": handle_report_step_cost,
    "disable_telemetry": handle_disable_telemetry,
}

# ---------------------------------------------------------------------------
# Server builder
# ---------------------------------------------------------------------------


def build_server(config: ServerConfig) -> Server:
    """Create and configure the MCP Server instance.

    Registers ``tools/list`` and ``tools/call`` handlers. Does not start I/O.

    Args:
        config: Resolved server configuration.

    Returns:
        A configured :class:`mcp.server.Server` instance.
    """
    server: Server = Server("tokencast-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="estimate_cost",
                description=(
                    "Estimate Anthropic API token costs for a development plan"
                    " before execution"
                ),
                inputSchema=ESTIMATE_COST_SCHEMA,
            ),
            Tool(
                name="get_calibration_status",
                description=(
                    "Get calibration health and accuracy metrics for cost estimates"
                ),
                inputSchema=GET_CALIBRATION_STATUS_SCHEMA,
            ),
            Tool(
                name="get_cost_history",
                description="Query historical cost estimation records and actuals",
                inputSchema=GET_COST_HISTORY_SCHEMA,
            ),
            Tool(
                name="report_session",
                description=(
                    "Report actual session cost to improve future calibration"
                ),
                inputSchema=REPORT_SESSION_SCHEMA,
            ),
            Tool(
                name="report_step_cost",
                description=(
                    "Record the cost of a completed pipeline step. "
                    "Costs accumulate per step and are flushed when report_session is called."
                ),
                inputSchema=REPORT_STEP_COST_SCHEMA,
            ),
            Tool(
                name="disable_telemetry",
                description=(
                    "Permanently disable anonymous telemetry. "
                    "Creates ~/.tokencast/no-telemetry file. "
                    "Use this to opt out of usage data collection."
                ),
                inputSchema=DISABLE_TELEMETRY_SCHEMA,
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Optional[dict[str, Any]]) -> list[TextContent]:
        args = arguments or {}
        try:
            if name not in _DISPATCH:
                raise ValueError(f"Unknown tool: {name!r}")
            handler = _DISPATCH[name]
            result = await handler(args, config)
            # Fire-and-forget telemetry after successful tool calls that
            # produce calibration data (estimate_cost and report_session).
            if name in ("estimate_cost", "report_session"):
                _telemetry.record_event(
                    name,
                    telemetry_enabled=config.telemetry_enabled,
                    calibration_dir=str(config.calibration_dir),
                    client_name=config.client_name,
                )
            return [TextContent(type="text", text=json.dumps(result))]
        except ValueError as exc:
            logger.warning("Tool call error (ValueError) for %r: %s", name, exc)
            return [TextContent(type="text", text=f"Error: {exc}")]
        except Exception as exc:
            logger.error(
                "Unexpected error in tool %r: %s", name, exc, exc_info=True
            )
            return [
                TextContent(
                    type="text",
                    text=f"Internal server error in tool {name!r}: {exc}",
                )
            ]

    return server


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------


async def run_server(config: ServerConfig) -> None:
    """Run the MCP server over stdio until the client disconnects.

    Args:
        config: Resolved server configuration.
    """
    config.ensure_dirs()
    server = build_server(config)
    logger.info(
        "tokencast-mcp %s started, calibration_dir=%s",
        __version__,
        config.calibration_dir,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


# Public re-export so `from tokencast_mcp import run` works (see __init__.py)
run = run_server


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed :class:`argparse.Namespace`.
    """
    parser = argparse.ArgumentParser(
        prog="tokencast-mcp",
        description="tokencast MCP server (stdio transport)",
    )
    parser.add_argument(
        "--calibration-dir",
        default=None,
        help="Path to calibration directory (default: ~/.tokencast/calibration)",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Project root for file measurement resolution",
    )
    parser.add_argument(
        "--no-telemetry",
        action="store_true",
        default=False,
        help=(
            "Disable anonymous usage telemetry. "
            "Telemetry is ON by default. "
            "Also disable via TOKENCAST_TELEMETRY=0 or the disable_telemetry MCP tool."
        ),
    )
    parser.add_argument(
        "--telemetry",
        action="store_const",
        const=True,
        default=None,
        help="(deprecated, no-op -- telemetry is on by default)",
    )
    parser.add_argument(
        "--no-cta",
        action="store_true",
        default=False,
        help=(
            "Suppress the team-sharing waitlist CTA in report_session responses. "
            "Also suppressed via TOKENCAST_NO_CTA=1 env var."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser.parse_args(argv)


def main(argv=None) -> None:
    """CLI entry point: parse args, build config, run the server.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).
    """
    args = parse_args(argv)
    config = ServerConfig.from_args(
        calibration_dir=args.calibration_dir,
        project_dir=args.project_dir,
        telemetry_enabled=not args.no_telemetry,
        no_cta=args.no_cta,
    )
    try:
        asyncio.run(run_server(config))
    except KeyboardInterrupt:
        pass
