"""Handler for the get_calibration_status MCP tool."""

import sys
from tokencast_mcp.config import ServerConfig
from tokencast.api import get_calibration_status as _api_get_calibration_status

# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

GET_CALIBRATION_STATUS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "window": {"type": "string"},
    },
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Text summary helpers
# ---------------------------------------------------------------------------


def _format_text_summary(result: dict) -> str:
    """Produce a concise human-readable summary of the status result.

    Used by LLM clients that prefer prose over raw JSON.
    """
    health = result.get("health") or {}
    status = health.get("status", "unknown")
    message = health.get("message", "")

    lines = [f"Calibration status: {status}"]
    if message:
        lines.append(message)

    accuracy = result.get("accuracy")
    if accuracy:
        mean_r = accuracy.get("mean_ratio")
        if mean_r is not None:
            lines.append(f"Mean accuracy ratio: {mean_r:.2f}x expected")
        trend = accuracy.get("trend")
        if trend:
            lines.append(f"Trend: {trend}")
        pct_exp = accuracy.get("pct_within_expected")
        if pct_exp is not None:
            lines.append(f"Within expected band: {pct_exp:.0%} of sessions")

    outliers = result.get("outliers")
    if outliers and outliers.get("count", 0) > 0:
        count = outliers["count"]
        rate = outliers.get("outlier_rate", 0)
        lines.append(f"Outliers: {count} session(s) ({rate:.0%} rate)")

    recs = result.get("recommendations") or []
    if recs:
        lines.append(f"Recommendations: {len(recs)} item(s)")
        for rec in recs[:3]:  # Show at most 3
            desc = rec.get("description", "")
            if desc:
                # Truncate long descriptions
                if len(desc) > 120:
                    desc = desc[:117] + "..."
                lines.append(f"  - {desc}")
        if len(recs) > 3:
            lines.append(f"  ... and {len(recs) - 3} more.")

    window = result.get("window") or {}
    total = window.get("total_records", 0)
    in_window = window.get("records_in_window", 0)
    if total > 0:
        lines.append(f"Records: {in_window} in window, {total} total")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def handle_get_calibration_status(params: dict, config: ServerConfig) -> dict:
    """Handle a get_calibration_status tool call.

    Delegates to ``tokencast.api.get_calibration_status``, which loads
    calibration history and factors and calls ``build_status_output()``.

    Args:
        params: Tool arguments from the MCP client.  Optional key ``window``
            accepts a spec like ``"30d"``, ``"10"``, or ``"all"``.
        config: Server runtime configuration (supplies ``calibration_dir``).

    Returns:
        Dict with calibration status data (``schema_version: 1``) plus a
        ``text_summary`` field for LLM clients.
    """
    print("[get_calibration_status] called", file=sys.stderr)

    calibration_dir = str(config.calibration_dir)
    result = _api_get_calibration_status(params, calibration_dir=calibration_dir)

    # Attach human-readable summary for LLM clients
    result["text_summary"] = _format_text_summary(result)

    return result
