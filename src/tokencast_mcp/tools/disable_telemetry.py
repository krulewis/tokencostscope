"""Handler for the disable_telemetry MCP tool."""

import os
import pathlib
from tokencast_mcp.config import ServerConfig
from tokencast import telemetry as _telemetry

DISABLE_TELEMETRY_SCHEMA: dict = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


async def handle_disable_telemetry(params: dict, config: ServerConfig) -> dict:
    try:
        no_telemetry_path = _telemetry._NO_TELEMETRY_PATH
        no_telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = no_telemetry_path.parent / f"no-telemetry.tmp.{os.getpid()}"
        try:
            tmp_path.write_text("", encoding="utf-8")
            os.rename(str(tmp_path), str(no_telemetry_path))
        except OSError:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            if not no_telemetry_path.exists():
                raise
        return {
            "status": "disabled",
            "file": str(no_telemetry_path),
            "message": (
                f"Telemetry permanently disabled. "
                f"File created: {no_telemetry_path}\n"
                "To re-enable for a session: set TOKENCAST_TELEMETRY=1.\n"
                "Note: TOKENCAST_TELEMETRY=1 overrides this file — "
                "if that env var is set, telemetry will still be active.\n"
                "Note: your install ID (~/.tokencast/install_id) was not deleted — "
                "no events are sent while telemetry is disabled."
            ),
        }
    except OSError as exc:
        return {
            "status": "error",
            "message": (
                f"Failed to create no-telemetry file: {exc}. "
                "Alternative: set TOKENCAST_TELEMETRY=0 in your environment."
            ),
        }
