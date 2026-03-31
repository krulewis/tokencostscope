"""Canonical step name resolution for the tokencast MCP server.

Duplicates DEFAULT_AGENT_TO_STEP from scripts/sum-session-tokens.py into an
importable module so the MCP server can import it without depending on the
scripts/ directory.
"""

import json
from pathlib import Path

# Default agent-name → pipeline step mapping (hardcoded fallback only).
# Enterprise teams override via calibration/agent-map.json (E1).
# "engineer" is absent — ordinal disambiguation happens in _build_spans().
# Users should name agents "engineer-initial" / "engineer-final" to be unambiguous.
#
# NOTE: Keep this in sync with DEFAULT_AGENT_TO_STEP in scripts/sum-session-tokens.py.
DEFAULT_AGENT_TO_STEP: dict = {
    "researcher": "Research Agent",
    "research": "Research Agent",
    "architect": "Architect Agent",
    "engineer-initial": "Engineer Initial Plan",
    "engineer-final": "Engineer Final Plan",
    "staff-reviewer": "Staff Review",
    "staff_reviewer": "Staff Review",
    "implementer": "Implementation",
    "implement": "Implementation",
    "qa": "QA",
    "test-writing": "Test Writing",
    "test_writing": "Test Writing",
    "frontend-designer": "Frontend Designer",
    "frontend_designer": "Frontend Designer",
    "docs-updater": "Docs Updater",
    "docs_updater": "Docs Updater",
}

# Set of canonical step names (values from DEFAULT_AGENT_TO_STEP).
CANONICAL_STEP_NAMES: set = set(DEFAULT_AGENT_TO_STEP.values())

# The derived aggregate step name that generates a warning if reported.
PR_REVIEW_LOOP_NAME: str = "PR Review Loop"


def load_agent_map(calibration_dir) -> dict:
    """Load agent-name overrides from calibration/agent-map.json.

    Args:
        calibration_dir: Path to the calibration directory (str or Path).

    Returns:
        Dict of alias → canonical step name from the JSON file, or empty dict
        if the file is absent, unreadable, or contains invalid JSON.
    """
    try:
        path = Path(calibration_dir) / "agent-map.json"
        if not path.exists():
            return {}
        raw = json.loads(path.read_text())
        return {k.lower().strip(): v for k, v in raw.items()}
    except Exception:
        return {}


def resolve_step_name(raw_name: str, calibration_dir=None):
    """Resolve a raw step name to its canonical form.

    Resolution order per protocol Section 9:
    1. Load agent-map.json from calibration_dir (if provided) and merge with
       DEFAULT_AGENT_TO_STEP; config file wins for conflicting keys.
    2. Check if raw_name.strip().lower() matches any alias key in the merged
       map; if yes, return the canonical name.
    3. Check if raw_name is already a canonical step name (direct match in
       values set); if yes, return it unchanged.
    4. If raw_name == PR_REVIEW_LOOP_NAME (after strip), return
       (raw_name, "pr_review_loop_is_derived").
    5. Return (raw_name, None) — unknown names accepted as-is per protocol.

    Args:
        raw_name: The step name string to resolve.
        calibration_dir: Optional path to the calibration directory for
            loading agent-map.json overrides. If None, only the hardcoded
            defaults are used.

    Returns:
        Tuple of (canonical_name: str, warning: str | None).
        warning is None when no notable condition occurred.
    """
    stripped = raw_name.strip()
    lower = stripped.lower()

    # Build merged lookup: DEFAULT first, then config overrides win
    merged_map = dict(DEFAULT_AGENT_TO_STEP)
    if calibration_dir is not None:
        overrides = load_agent_map(calibration_dir)
        merged_map.update(overrides)
    all_canonical = set(merged_map.values())

    # Step 2: alias lookup (case-insensitive on key)
    if lower in merged_map:
        return (merged_map[lower], None)

    # Step 3: already a canonical name (case-sensitive direct match in merged values)
    if stripped in all_canonical:
        return (stripped, None)

    # Step 4: PR Review Loop special case
    if stripped == PR_REVIEW_LOOP_NAME:
        return (stripped, "pr_review_loop_is_derived")

    # Step 5: unknown name — accepted as-is
    return (stripped, None)
