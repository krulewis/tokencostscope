"""tokencast_mcp — MCP server exposing tokencast tools over stdio transport."""

__version__ = "0.1.1"

from tokencast_mcp.server import run  # noqa: F401 — re-export for `from tokencast_mcp import run`
