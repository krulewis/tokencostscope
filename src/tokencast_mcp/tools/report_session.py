"""MCP tool handler for report_session (US-1b.07)."""

from tokencast_mcp.config import ServerConfig
from tokencast.api import report_session as _api_report_session

# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

REPORT_SESSION_SCHEMA: dict = {
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

# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def handle_report_session(params: dict, config: ServerConfig) -> dict:
    """Handle a report_session tool call.

    Delegates to ``api.report_session()`` with the server's calibration
    directory. Returns a protocol-compliant response dict.

    Args:
        params: Tool arguments from the MCP client. Required key:
            ``actual_cost`` (float >= 0). Optional keys: ``step_actuals``
            (dict), ``turn_count`` (int), ``review_cycles_actual`` (int).
        config: Server runtime configuration.

    Returns:
        Protocol-compliant dict with ``attribution_protocol_version``,
        ``record_written``, ``attribution_method``, ``actual_cost``,
        ``step_actuals``, and optionally ``warning``.

    Raises:
        ValueError: If validation fails (missing or invalid ``actual_cost``,
            negative ``step_actuals`` values, etc.).
    """
    result = _api_report_session(params, calibration_dir=str(config.calibration_dir))
    if "error" in result:
        raise ValueError(result.get("message", result.get("error", "Unknown error")))
    return result
