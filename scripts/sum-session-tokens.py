#!/usr/bin/env python3
"""Sum token usage from a Claude Code session JSONL file and compute actual dollar cost.

Usage:
    python3 sum-session-tokens.py <jsonl_path> [baseline_cost] [sidecar_path]

If sidecar_path is provided and exists, uses sum_session_by_agent() to attribute costs
per pipeline step. Otherwise falls back to sum_session() session totals only.

Output: JSON object with actual cost and metadata.
baseline_cost is subtracted from total to isolate the task's cost (tokens used
before the estimate was created are not part of the task).
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

# Prices per million tokens — update when pricing changes.
# Must match references/pricing.md.
PRICES = {
    "claude-sonnet-4-6": {
        "input": 3.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
        "output": 15.00,
    },
    "claude-opus-4-6": {
        "input": 15.00,
        "cache_read": 1.50,
        "cache_write": 18.75,
        "output": 75.00,
    },
    "claude-haiku-4-5": {
        "input": 0.80,
        "cache_read": 0.08,
        "cache_write": 1.00,
        "output": 4.00,
    },
}

# Fallback for unknown models
DEFAULT_MODEL = "claude-sonnet-4-6"

# Default agent-name → pipeline step mapping (hardcoded fallback only).
# Enterprise teams override via calibration/agent-map.json (E1).
# "engineer" is absent — ordinal disambiguation happens in _build_spans().
# Users should name agents "engineer-initial" / "engineer-final" to be unambiguous.
DEFAULT_AGENT_TO_STEP = {
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
    "frontend-designer": "Frontend Designer",
    "frontend_designer": "Frontend Designer",
    "docs-updater": "Docs Updater",
    "docs_updater": "Docs Updater",
}


def compute_line_cost(obj: dict) -> float:
    """Compute dollar cost for one parsed JSONL assistant message object.

    Returns 0.0 if the object is not a billable assistant message.
    Used by both sum_session() and sum_session_by_agent() to ensure cost
    calculations are identical and per-agent totals sum to the session total.
    """
    if obj.get("type") != "assistant":
        return 0.0
    msg = obj.get("message", {})
    usage = msg.get("usage")
    if not usage:
        return 0.0
    model = msg.get("model", "")
    if not model or model == "<synthetic>":
        return 0.0
    model_key = model
    for known in PRICES:
        if known in model:
            model_key = known
            break
    prices = PRICES.get(model_key, PRICES[DEFAULT_MODEL])
    inp = usage.get("input_tokens", 0)
    cr = usage.get("cache_read_input_tokens", 0)
    cw = usage.get("cache_creation_input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cost = (
        inp * prices["input"]
        + cr * prices["cache_read"]
        + cw * prices["cache_write"]
        + out * prices["output"]
    ) / 1_000_000
    return cost


def sum_session(jsonl_path: str, baseline_cost: float = 0.0) -> dict:
    """Sum all billable turns in a session JSONL. Returns session-level totals."""
    total_cost = 0.0
    turn_count = 0

    with open(jsonl_path) as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            cost = compute_line_cost(obj)
            if cost > 0:
                total_cost += cost
                turn_count += 1

    task_cost = max(0.0, total_cost - baseline_cost)

    return {
        "total_session_cost": round(total_cost, 4),
        "actual_cost": round(task_cost, 4),
        "baseline_cost": round(baseline_cost, 4),
        "turn_count": turn_count,
    }


def _load_agent_map(calibration_dir: str) -> dict:
    """Load agent-to-step mapping. Merges DEFAULT_AGENT_TO_STEP with calibration/agent-map.json.

    The config file wins over defaults for any key that appears in both.
    Keys in DEFAULT_AGENT_TO_STEP that do not appear in the config are preserved.
    Enterprise teams only need to specify their custom names — they do not need to
    replicate the full default table.

    Returns the merged dict. If agent-map.json is absent or malformed, returns
    DEFAULT_AGENT_TO_STEP unchanged (fail-open: missing config is not an error).
    """
    merged = dict(DEFAULT_AGENT_TO_STEP)
    if not calibration_dir:
        return merged
    map_path = os.path.join(calibration_dir, "agent-map.json")
    if not os.path.exists(map_path):
        return merged
    try:
        with open(map_path) as f:
            overrides = json.load(f)
        if isinstance(overrides, dict):
            # Lowercase all override keys for consistent lookup
            merged.update({k.lower().strip(): v for k, v in overrides.items()})
    except (json.JSONDecodeError, OSError):
        pass  # fail-open: return defaults
    return merged


def _subtract_ranges(start: int, end: int, children: list) -> list:
    """Return non-overlapping line ranges after subtracting child ranges from [start, end).

    children is a list of (child_start, child_end) tuples.
    Returns a list of (range_start, range_end) tuples representing the gaps.
    """
    if not children:
        return [(start, end)]

    # Sort children by start, then merge overlapping children
    sorted_children = sorted(children)
    merged_children = []
    for cs, ce in sorted_children:
        # Clamp child to parent range
        cs = max(cs, start)
        ce = min(ce, end)
        if cs >= ce:
            continue
        if merged_children and cs <= merged_children[-1][1]:
            merged_children[-1] = (merged_children[-1][0], max(merged_children[-1][1], ce))
        else:
            merged_children.append((cs, ce))

    result = []
    cursor = start
    for cs, ce in merged_children:
        if cursor < cs:
            result.append((cursor, cs))
        cursor = max(cursor, ce)
    if cursor < end:
        result.append((cursor, end))
    return result


def _build_spans(sidecar_path: str, agent_to_step: dict) -> dict:
    """Parse sidecar JSONL and return effective_ranges: {step_name: [(start, end), ...]}

    Nesting inference (F1): open span = open agent_start with no matching stop yet.
    When a new agent_start fires, the innermost open span is its parent.
    Child ranges are subtracted from parent effective ranges.

    FIFO matching (F2): for each agent_stop, pop oldest unmatched start for same agent_name
    by span_id order.

    Engineer disambiguation (F8): first "engineer" → Initial Plan, second → Final Plan,
    third+ → raw name. "engineer-initial" / "engineer-final" map directly via agent_to_step.
    """
    events = []
    with open(sidecar_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("schema_version") != 1:
                continue
            if ev.get("type") not in ("agent_start", "agent_stop"):
                continue
            events.append(ev)

    events.sort(key=lambda e: (e.get("span_id", 0), e.get("timestamp", "")))

    open_starts = defaultdict(list)  # agent_name → [start_event, ...]
    open_spans = []                  # (agent_name, start_line, span_id) — ordered by start
    completed_spans = []             # {step_name, start_line, end_line, parent_name}
    engineer_count = 0

    for ev in events:
        ev_type = ev.get("type")
        agent_name = ev.get("agent_name", "")
        line_count = ev.get("jsonl_line_count", 0)
        span_id = ev.get("span_id", 0)

        if ev_type == "agent_start":
            parent_name = open_spans[-1][0] if open_spans else None
            parent_start_line = open_spans[-1][1] if open_spans else None
            open_starts[agent_name].append({
                "start_line": line_count,
                "span_id": span_id,
                "parent_name": parent_name,
                "parent_start_line": parent_start_line,
            })
            open_spans.append((agent_name, line_count, span_id))

        elif ev_type == "agent_stop":
            if not open_starts[agent_name]:
                continue
            # FIFO: pop oldest unmatched start for this agent_name (F2)
            start_ev = open_starts[agent_name].pop(0)
            open_spans = [
                (n, sl, sid) for (n, sl, sid) in open_spans
                if not (n == agent_name and sid == start_ev["span_id"])
            ]

            # Resolve step name — engineer ordinal disambiguation (F8)
            step_name = agent_to_step.get(agent_name)
            if agent_name == "engineer":
                engineer_count += 1
                if engineer_count == 1:
                    step_name = "Engineer Initial Plan"
                elif engineer_count == 2:
                    step_name = "Engineer Final Plan"
                else:
                    step_name = agent_name
            elif step_name is None:
                step_name = agent_name

            completed_spans.append({
                "step_name": step_name,
                "start_line": start_ev["start_line"],
                "end_line": line_count,
                "parent_name": start_ev["parent_name"],
                "parent_start_line": start_ev.get("parent_start_line"),
            })

    # Unmatched starts: give end_line = last recorded line count
    total_lines = max((ev.get("jsonl_line_count", 0) for ev in events), default=0)
    for agent_name, starts_list in open_starts.items():
        for start_ev in starts_list:
            if agent_name == "engineer":
                engineer_count += 1
                if engineer_count == 1:
                    step_name = "Engineer Initial Plan"
                elif engineer_count == 2:
                    step_name = "Engineer Final Plan"
                else:
                    step_name = agent_name
            else:
                step_name = agent_to_step.get(agent_name, agent_name)
            completed_spans.append({
                "step_name": step_name,
                "start_line": start_ev["start_line"],
                "end_line": total_lines,
                "parent_name": start_ev["parent_name"],
                "parent_start_line": start_ev.get("parent_start_line"),
            })

    # Build effective ranges: subtract child spans from parent spans
    spans_by_step = {}
    all_child_ranges = {}  # step_name (parent) → [(start, end)]
    # Build start_line → step_name lookup for parent resolution (handles engineer ordinal disambiguation)
    start_to_step = {sp["start_line"]: sp["step_name"] for sp in completed_spans}
    for sp in completed_spans:
        step = sp["step_name"]
        spans_by_step.setdefault(step, []).append((sp["start_line"], sp["end_line"]))
        if sp["parent_name"]:
            # Resolve parent to its step_name via start_line lookup (handles engineer ordinal
            # disambiguation — agent_to_step doesn't contain "engineer" directly)
            parent_sl = sp.get("parent_start_line")
            if parent_sl is not None and parent_sl in start_to_step:
                parent_step = start_to_step[parent_sl]
            else:
                parent_step = agent_to_step.get(sp["parent_name"], sp["parent_name"])
            all_child_ranges.setdefault(parent_step, []).append(
                (sp["start_line"], sp["end_line"])
            )

    effective_ranges = {}
    for step_name, raw_ranges in spans_by_step.items():
        child_ranges = all_child_ranges.get(step_name, [])
        result_ranges = []
        for (rs, re) in raw_ranges:
            result_ranges.extend(_subtract_ranges(rs, re, child_ranges))
        effective_ranges[step_name] = sorted(result_ranges)

    return effective_ranges


def sum_session_by_agent(
    jsonl_path: str,
    sidecar_path: str,
    baseline_cost: float = 0.0,
    calibration_dir: Optional[str] = None,
) -> dict:
    """Single-pass JSONL cost attribution by agent span (F4).

    calibration_dir is used to load agent-map.json (E1). If None, inferred from
    sidecar_path's parent directory.
    """
    cal_dir = calibration_dir or str(Path(sidecar_path).parent)
    agent_to_step = _load_agent_map(cal_dir)
    effective_ranges = _build_spans(sidecar_path, agent_to_step)

    # Build sorted flat list of (start_line, end_line, step_name) for O(m) lookup per line
    # (m = number of ranges); total attribution pass is O(n×m) over n JSONL lines
    all_ranges = sorted(
        (s, e, step)
        for step, ranges in effective_ranges.items()
        for (s, e) in ranges
    )

    def find_step(line_num: int) -> str:
        for (s, e, step) in all_ranges:
            if s <= line_num < e:
                return step
        return "_orchestrator"

    # Single pass (F4) — same compute_line_cost() as sum_session() for consistency
    step_costs: dict = {}
    total_cost = 0.0
    turn_count = 0
    line_num = 0
    with open(jsonl_path) as f:
        for raw_line in f:
            line_num += 1
            try:
                obj = json.loads(raw_line.strip())
            except json.JSONDecodeError:
                continue
            cost = compute_line_cost(obj)
            if cost > 0:
                step = find_step(line_num)
                step_costs[step] = step_costs.get(step, 0.0) + cost
                total_cost += cost
                turn_count += 1

    task_cost = max(0.0, total_cost - baseline_cost)
    # Scale step costs proportionally to exclude baseline
    scale = task_cost / total_cost if total_cost > 0 else 0.0
    step_actuals = (
        {step: round(cost * scale, 4) for step, cost in step_costs.items()}
        if step_costs else None
    )

    return {
        "total_session_cost": round(total_cost, 4),
        "actual_cost": round(task_cost, 4),
        "baseline_cost": round(baseline_cost, 4),
        "turn_count": turn_count,
        "step_actuals": step_actuals,
    }


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: sum-session-tokens.py <jsonl_path> [baseline_cost] [sidecar_path]",
            file=sys.stderr,
        )
        sys.exit(1)

    jsonl_path = sys.argv[1]
    baseline_cost = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    # F7: explicit sidecar_path check — None if absent or empty string
    sidecar_path = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None

    if not Path(jsonl_path).exists():
        print(json.dumps({"error": f"File not found: {jsonl_path}"}))
        sys.exit(1)

    if sidecar_path and Path(sidecar_path).exists():
        calibration_dir = str(Path(sidecar_path).parent)
        result = sum_session_by_agent(jsonl_path, sidecar_path, baseline_cost, calibration_dir)
    else:
        result = sum_session(jsonl_path, baseline_cost)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
