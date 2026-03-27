"""MCP tool handler for report_step_cost (US-1c.02)."""

from tokencast_mcp.config import ServerConfig
from tokencast.api import report_step_cost as _api_report_step_cost

# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

REPORT_STEP_COST_SCHEMA: dict = {
    "type": "object",
    "required": ["step_name"],
    "properties": {
        "step_name": {"type": "string"},
        "cost": {"type": "number", "minimum": 0},
        "tokens_in": {"type": "integer", "minimum": 0},
        "tokens_out": {"type": "integer", "minimum": 0},
        "tokens_cache_read": {"type": "integer", "minimum": 0},
        "tokens_cache_write": {"type": "integer", "minimum": 0},
        "model": {"type": "string"},
    },
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def handle_report_step_cost(params: dict, config: ServerConfig) -> dict:
    """Handle a report_step_cost tool call.

    Args:
        params: Tool arguments from the MCP client.
        config: Server runtime configuration.

    Returns:
        Protocol-compliant dict with attribution_protocol_version, step_name,
        cost_this_call, cumulative_step_cost, total_session_accumulated, and
        optionally warning.

    Raises:
        ValueError: If the call fails validation or no active estimate exists.
    """
    result = _api_report_step_cost(params, calibration_dir=str(config.calibration_dir))
    if "error" in result:
        raise ValueError(result.get("message", result.get("error", "Unknown error")))
    return result
