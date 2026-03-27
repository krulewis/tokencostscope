"""Handler for the get_cost_history MCP tool."""

from tokencast_mcp.config import ServerConfig
from tokencast.api import get_cost_history as _api_get_cost_history

# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

GET_COST_HISTORY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "window": {"type": "string"},
        "include_outliers": {"type": "boolean"},
    },
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def handle_get_cost_history(params: dict, config: ServerConfig) -> dict:
    """Handle a get_cost_history tool call.

    Delegates to :func:`tokencast.api.get_cost_history` with the calibration
    directory resolved from ``config``.

    Args:
        params: Tool arguments from the MCP client.
        config: Server runtime configuration (supplies ``calibration_dir``).

    Returns:
        Dict with ``records`` list and ``summary`` statistics dict.
    """
    return _api_get_cost_history(
        params,
        calibration_dir=str(config.calibration_dir),
    )
