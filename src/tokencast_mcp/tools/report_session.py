"""MCP tool handler for report_session (US-1b.07)."""

import os

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


def _get_session_count(config: ServerConfig) -> int:
    """Return the number of history records on disk before this call."""
    try:
        history_path = config.history_path
        if not history_path.exists():
            return 0
        count = 0
        with history_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count
    except Exception:
        return 0


async def handle_report_session(params: dict, config: ServerConfig) -> dict:
    """Handle a report_session tool call.

    Delegates to ``api.report_session()`` with the server's calibration
    directory. Returns a protocol-compliant response dict.

    CTA behaviour: after a successful record write, if the session count
    reaches the threshold the response includes a ``team_sharing_cta`` field.
    The CTA is shown at most once per server process (tracked via
    ``config.cta_shown``). It is suppressed when ``config.no_cta`` is True
    or the ``TOKENCAST_NO_CTA`` environment variable is set to exactly ``"1"``
    (consistent with the ``TOKENCAST_TELEMETRY`` opt-in convention).

    Args:
        params: Tool arguments from the MCP client. Required key:
            ``actual_cost`` (float >= 0). Optional keys: ``step_actuals``
            (dict), ``turn_count`` (int), ``review_cycles_actual`` (int).
        config: Server runtime configuration.

    Returns:
        Protocol-compliant dict with ``attribution_protocol_version``,
        ``record_written``, ``attribution_method``, ``actual_cost``,
        ``step_actuals``, and optionally ``warning`` and/or
        ``team_sharing_cta``.

    Raises:
        ValueError: If validation fails (missing or invalid ``actual_cost``,
            negative ``step_actuals`` values, etc.).
    """
    # Determine suppression: --no-cta flag or TOKENCAST_NO_CTA env var.
    # Requires exactly "1" to match TOKENCAST_TELEMETRY opt-out convention.
    env_no_cta = os.environ.get("TOKENCAST_NO_CTA") == "1"
    suppress_cta = config.no_cta or env_no_cta or config.cta_shown

    # Count sessions before appending this one.
    session_count = _get_session_count(config)

    result = _api_report_session(
        params,
        calibration_dir=str(config.calibration_dir),
        session_count=session_count,
        suppress_cta=suppress_cta,
    )
    if "error" in result:
        raise ValueError(result.get("message", result.get("error", "Unknown error")))

    # Mark CTA as shown for this server session so it doesn't repeat.
    if "team_sharing_cta" in result:
        config.cta_shown = True

    return result
