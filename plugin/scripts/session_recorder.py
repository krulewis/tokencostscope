"""session_recorder.py — Pure-computation history record builder.

Extracted from the learn.sh RECORD block (lines 151-240) to share the record
construction logic between learn.sh (sidecar path) and api.py report_session
(MCP path). No file I/O is performed in this module.

Public API:
    build_history_record(estimate, actual_cost, ...) -> dict
"""
# stdlib-only module: used standalone by plugin hooks. Do not add non-stdlib imports.

from datetime import datetime, timezone
from typing import Optional


def build_history_record(
    estimate: dict,
    actual_cost: float,
    turn_count: int = 0,
    review_cycles_actual: Optional[int] = None,
    step_actuals_mcp: Optional[dict] = None,
    step_actuals_sidecar: Optional[dict] = None,
    timestamp: Optional[str] = None,
) -> dict:
    """Build a calibration history record dict from session data.

    Attribution priority (first non-None, non-empty wins):
      1. step_actuals_mcp  -> attribution_method = "mcp"
      2. step_actuals_sidecar -> attribution_method = "sidecar"
      3. proportional fallback -> attribution_method = "proportional"

    Args:
        estimate: Contents of active-estimate.json (or {} for unknown sessions).
        actual_cost: Total session cost in dollars (post-baseline, already computed).
        turn_count: Number of billable turns. Default 0.
        review_cycles_actual: Actual PR review cycles. None if unknown.
                              Callers should convert 0 to None (learn.sh convention).
        step_actuals_mcp: Step costs from MCP report_step_cost accumulation.
                         None or empty dict means "not provided".
        step_actuals_sidecar: Step costs from sidecar JSONL span attribution.
                              None or empty dict means "not provided".
        timestamp: ISO 8601 UTC string. Auto-generated if None.

    Returns:
        dict with all 26 history record fields. No file I/O is performed.
    """
    # --- Normalize review_cycles_actual: 0 means unknown (matches learn.sh convention) ---
    if review_cycles_actual == 0:
        review_cycles_actual = None

    # --- Timestamp ---
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Extract fields from estimate with fallbacks matching learn.sh lines 65-76 ---
    size = estimate.get("size") or "M"
    files = estimate.get("files") or 0
    complexity = estimate.get("complexity") or "medium"
    project_type = estimate.get("project_type") or "unknown"
    language = estimate.get("language") or "unknown"
    steps = estimate.get("steps", [])
    step_count = int(estimate.get("step_count", 0) or 0)
    review_cycles_estimated = int(estimate.get("review_cycles_estimated", 0) or 0)
    parallel_groups = estimate.get("parallel_groups", [])
    optimistic_cost = float(estimate.get("optimistic_cost", 0) or 0)
    pessimistic_cost = float(estimate.get("pessimistic_cost", 0) or 0)
    file_brackets = estimate.get("file_brackets")
    files_measured = int(estimate.get("files_measured", 0) or 0)
    continuation = estimate.get("continuation", False)

    # --- Pipeline signature: same formula as learn.sh line 62 ---
    pipeline_signature = "+".join(sorted(s.lower().replace(" ", "_") for s in steps))

    # --- parallel_steps_detected: coerce same as learn.sh line 231 ---
    psd_raw = estimate.get("parallel_steps_detected", 0)
    psd_str = str(psd_raw)
    if psd_str.lstrip("-").isdigit():
        parallel_steps_detected = int(psd_str)
    elif psd_str.lower() in ("true", "yes", "1"):
        parallel_steps_detected = 1
    else:
        parallel_steps_detected = 0

    # --- step_costs_estimated: exclude PR Review Loop key (learn.sh lines 167-170) ---
    PR_REVIEW_LOOP_KEY = "PR Review Loop"
    step_costs_raw = estimate.get("step_costs", {})
    step_costs_estimated = {
        k: v for k, v in step_costs_raw.items() if k != PR_REVIEW_LOOP_KEY
    }

    # --- Attribution: mcp wins over sidecar wins over proportional ---
    if step_actuals_mcp:
        step_actuals = step_actuals_mcp
        attribution_method = "mcp"
    elif step_actuals_sidecar:
        step_actuals = step_actuals_sidecar
        attribution_method = "sidecar"
    else:
        step_actuals = {}
        attribution_method = "proportional"

    # --- session_expected: floor at 0.001 (learn.sh line 178) ---
    expected_cost_raw = float(estimate.get("expected_cost", 0) or 0)
    session_expected = max(expected_cost_raw, 0.001)

    # --- ratio ---
    actual = float(actual_cost)
    ratio = round(actual / session_expected, 4)

    # --- step_ratios (learn.sh lines 180-189) ---
    if step_actuals and step_costs_estimated:
        step_ratios = {}
        for step_name, estimated in step_costs_estimated.items():
            actual_step = step_actuals.get(step_name, 0)
            if estimated > 0 and actual_step > 0:
                step_ratios[step_name] = round(actual_step / estimated, 4)
    else:
        # Proportional fallback: uniform ratio for all estimated steps
        session_ratio = round(actual / session_expected, 4)
        step_ratios = {step: session_ratio for step in step_costs_estimated}

    # --- step_actuals field in record: None when proportional (learn.sh line 236) ---
    record_step_actuals = step_actuals if step_actuals else None

    return {
        "timestamp": timestamp,
        "size": size,
        "files": int(files),
        "complexity": complexity,
        "expected_cost": expected_cost_raw,
        "optimistic_cost": optimistic_cost,
        "pessimistic_cost": pessimistic_cost,
        "actual_cost": actual,
        "ratio": ratio,
        "turn_count": int(turn_count),
        "steps": steps,
        "pipeline_signature": pipeline_signature,
        "project_type": project_type,
        "language": language,
        "step_count": step_count,
        "review_cycles_estimated": review_cycles_estimated,
        "review_cycles_actual": review_cycles_actual,
        "parallel_groups": parallel_groups,
        "parallel_steps_detected": parallel_steps_detected,
        "file_brackets": file_brackets,
        "files_measured": files_measured,
        "step_costs_estimated": step_costs_estimated,
        "step_ratios": step_ratios,
        "step_actuals": record_step_actuals,
        "attribution_method": attribution_method,
        "continuation": continuation,
    }
