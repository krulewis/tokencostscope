"""Handler for the estimate_cost MCP tool."""

import sys

from tokencast_mcp.config import ServerConfig
from tokencast.api import estimate_cost as _api_estimate_cost

# ---------------------------------------------------------------------------
# Input schema (JSON Schema object registered with the MCP server)
# ---------------------------------------------------------------------------

ESTIMATE_COST_SCHEMA: dict = {
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
        "parallel_groups": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "string"}},
        },
        "file_paths": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

_VALID_SIZES = {"XS", "S", "M", "L"}
_VALID_COMPLEXITIES = {"low", "medium", "high"}

# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def handle_estimate_cost(params: dict, config: ServerConfig) -> dict:
    """Handle an estimate_cost tool call.

    Validates required parameters, runs file measurement if file_paths is
    provided, calls the tokencast public API, writes calibration files, and
    returns the formatted result.

    Args:
        params: Tool arguments from the MCP client.
        config: Server runtime configuration (calibration paths, project dir).

    Returns:
        Dict with keys ``version``, ``estimate``, ``steps``, ``metadata``,
        ``step_costs``, and ``text`` (markdown table string).

    Raises:
        ValueError: If required parameters are missing or invalid.
    """
    # --- Required field presence ---
    if "size" not in params:
        raise ValueError("Missing required parameter: 'size'")
    if "files" not in params:
        raise ValueError("Missing required parameter: 'files'")
    if "complexity" not in params:
        raise ValueError("Missing required parameter: 'complexity'")

    # --- Value validation ---
    size = params["size"]
    if size not in _VALID_SIZES:
        raise ValueError(
            f"Invalid value for 'size': {size!r}. Must be one of: "
            + ", ".join(sorted(_VALID_SIZES))
        )

    files = params["files"]
    if not isinstance(files, int) or files < 0:
        raise ValueError(
            f"Invalid value for 'files': {files!r}. Must be a non-negative integer."
        )

    complexity = params["complexity"]
    if complexity not in _VALID_COMPLEXITIES:
        raise ValueError(
            f"Invalid value for 'complexity': {complexity!r}. Must be one of: "
            + ", ".join(sorted(_VALID_COMPLEXITIES))
        )

    print(
        f"[estimate_cost] called with size={size}, files={files}",
        file=sys.stderr,
    )

    # --- Delegate to API (handles file measurement and calibration file writes) ---
    project_dir_str = str(config.project_dir) if config.project_dir else None
    result = _api_estimate_cost(
        params,
        calibration_dir=str(config.calibration_dir),
        project_dir=project_dir_str,
    )

    # --- Build text summary ---
    result["text"] = _format_markdown_table(result)

    # First-run welcome note (US-PL-06)
    history_path = config.calibration_dir / "history.jsonl"
    if not history_path.exists() or history_path.stat().st_size == 0:
        result["text"] += (
            "\n**First run:** No calibration data yet -- estimates use defaults. "
            "Accuracy improves after 3+ sessions with auto-learning.\n"
        )

    return result


def _format_markdown_table(result: dict) -> str:
    """Format the SKILL.md output template markdown table string."""
    meta     = result["metadata"]
    estimate = result["estimate"]
    steps    = result["steps"]
    version  = result.get("version", "2.1.0")

    # Header
    size       = meta["size"]
    files      = meta["files"]
    complexity = meta["complexity"]
    proj_type  = meta["project_type"]
    language   = meta["language"]
    fb         = meta["file_brackets"]
    measured   = meta["files_measured"]

    if fb is None:
        files_line = f"**Files:** {files} total (all defaulted to medium — no paths extracted)"
    else:
        small  = fb.get("small",  0)
        medium = fb.get("medium", 0)
        large  = fb.get("large",  0)
        defaulted = files - measured
        files_line = (
            f"**Files:** {files} total "
            f"({measured} measured: {small} small, {medium} medium, {large} large; "
            f"{max(defaulted, 0)} defaulted to medium)"
        )

    step_names = [s["name"] for s in steps if s["name"] != "PR Review Loop"]
    step_count = len(step_names)
    steps_list_str = ", ".join(step_names)
    pricing_date = meta["pricing_last_updated"]
    rc = meta["review_cycles"]

    lines = [
        f"## tokencast estimate (v{version})",
        "",
        f"**Change:** size={size}, files={files}, complexity={complexity}, type={proj_type}, lang={language}",
        files_line,
        f"**Steps:** {steps_list_str} ({step_count} steps)",
        f"**Pricing:** last updated {pricing_date}",
    ]

    if meta.get("pricing_stale"):
        lines.append("**WARNING: Pricing data may be stale (>90 days old). Check references/pricing.md.**")

    lines += [
        "",
        "| Step                   | Model       | Cal    | Optimistic | Expected | Pessimistic |",
        "|------------------------|-------------|--------|------------|----------|-------------|",
    ]

    # Build parallel group prefixes
    parallel_groups = meta.get("parallel_groups") or []
    # Map step_name → box-drawing prefix
    prefix_map = {}
    for group in parallel_groups:
        for i, step_name in enumerate(group):
            if len(group) == 1:
                prefix_map[step_name] = ""
            elif i == 0:
                prefix_map[step_name] = "┌ "
            elif i == len(group) - 1:
                prefix_map[step_name] = "└ "
            else:
                prefix_map[step_name] = "│ "

    for step in steps:
        name     = step["name"]
        model    = step["model"]
        cal      = step["cal"]
        opt      = step["optimistic"]
        exp      = step["expected"]
        pess     = step["pessimistic"]
        prefix   = prefix_map.get(name, "")
        display  = f"{prefix}{name}"
        lines.append(
            f"| {display:<22} | {model:<11} | {cal:<6} | ${opt:>9.2f} | ${exp:>8.2f} | ${pess:>11.2f} |"
        )

    # Totals row
    total_opt  = estimate["optimistic"]
    total_exp  = estimate["expected"]
    total_pess = estimate["pessimistic"]
    lines.append(
        f"| **{'TOTAL':<20}** |             |        | **${total_opt:>8.2f}** | **${total_exp:>7.2f}** | **${total_pess:>10.2f}** |"
    )
    lines.append("Cal: S=per-step  P=per-signature  Z=size-class  G=global  --=uncalibrated")
    lines.append("")

    # Bands line
    if rc > 0:
        lines.append(
            f"**Bands:** Optimistic (1 review cycle) · Expected ({rc} cycles) · Pessimistic ({rc*2} cycles)"
        )
    else:
        lines.append(
            "**Bands:** Optimistic (best case) · Expected (typical) · Pessimistic (with rework)"
        )

    lines.append("**Tracking:** Estimate recorded. Actuals will be captured automatically at session end.")
    lines.append("**Tip:** Call `report_session` after your session completes to improve future estimates via calibration.")

    return "\n".join(lines) + "\n"
