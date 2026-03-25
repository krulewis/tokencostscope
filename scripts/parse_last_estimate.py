"""parse_last_estimate.py — Parse last-estimate.md into a minimal active-estimate.json structure.

Called by tokencostscope-learn.sh in the continuation reconstitution path when
active-estimate.json is absent but a recent last-estimate.md exists.

Public API:
    parse(content: str, max_age_hours: float, mtime: float | None) -> dict | None
"""

import re
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional


def parse(content: str, max_age_hours: float = 48.0, mtime: Optional[float] = None) -> Optional[dict]:
    """Parse last-estimate.md content into an active-estimate.json compatible dict.

    Args:
        content: String content of last-estimate.md.
        max_age_hours: Recency window in hours. Files older than this return None.
        mtime: File modification time as seconds since epoch. When None, recency
               check is skipped (useful for unit testing parse logic directly).

    Returns:
        Dict compatible with active-estimate.json schema, or None if:
        - File is stale (age > max_age_hours) and mtime is not None
        - Any required field is missing
        - expected_cost is 0 or absent
    """
    # Recency check
    if mtime is not None:
        age_seconds = time.time() - mtime
        if age_seconds > max_age_hours * 3600:
            return None

    # Parsed values — required fields start as None, optional have defaults
    size = None
    files = None
    complexity = None
    project_type = "unknown"
    language = "unknown"
    steps = []
    optimistic_cost = None
    expected_cost = None
    pessimistic_cost = None
    baseline_cost = 0.0
    review_cycles_estimated = 0
    parallel_steps_detected = 0

    for line in content.splitlines():
        # Compound metadata line: **Size:** M | **Files:** 28 | **Complexity:** medium
        m = re.search(
            r'\*\*Size:\*\*\s*(\S+)\s*\|\s*\*\*Files:\*\*\s*(\d+)\s*\|\s*\*\*Complexity:\*\*\s*(\S+)',
            line,
        )
        if m and size is None:
            size = m.group(1)
            files = int(m.group(2))
            complexity = m.group(3)
            continue

        # Compound metadata line: **Type:** greenfield | **Language:** python
        m = re.search(r'\*\*Type:\*\*\s*(\S+)\s*\|\s*\*\*Language:\*\*\s*(\S+)', line)
        if m and project_type == "unknown":
            project_type = m.group(1)
            language = m.group(2)
            continue

        # Steps line: **Steps:** Research Agent, Implementation, QA
        m = re.search(r'\*\*Steps:\*\*\s*(.+)', line)
        if m and not steps:
            raw = m.group(1).strip()
            # Split on ", " — step names controlled by SKILL.md template and never contain ", "
            steps = [s.strip() for s in raw.split(", ") if s.strip()]
            continue

        # Baseline cost — bold metadata style: **Baseline Cost:** $0.05
        m = re.search(r'\*\*Baseline Cost:\*\*\s*\$?([\d.]+)', line)
        if m:
            try:
                baseline_cost = float(m.group(1))
            except ValueError:
                pass
            continue

        # Baseline cost — footer plain style: Baseline Cost: $0.05
        m = re.search(r'^Baseline Cost:\s*\$?([\d.]+)', line)
        if m:
            try:
                baseline_cost = float(m.group(1))
            except ValueError:
                pass
            continue

        # Cost table rows
        m = re.search(r'\|\s*Optimistic\s*\|\s*\$?([\d.]+)\s*\|', line)
        if m and optimistic_cost is None:
            try:
                optimistic_cost = float(m.group(1))
            except ValueError:
                pass
            continue

        m = re.search(r'\|\s*Expected\s*\|\s*\$?([\d.]+)\s*\|', line)
        if m and expected_cost is None:
            try:
                expected_cost = float(m.group(1))
            except ValueError:
                pass
            continue

        # Pessimistic — |? handles tight formatting like | Pessimistic| $42.70|
        m = re.search(r'\|\s*Pessimistic\s*\|?\s*\$?([\d.]+)\s*\|?', line)
        if m and pessimistic_cost is None:
            try:
                pessimistic_cost = float(m.group(1))
            except ValueError:
                pass
            continue

        # Footer lines
        m = re.search(r'Review cycles estimated:\s*(\d+)', line)
        if m:
            try:
                review_cycles_estimated = int(m.group(1))
            except ValueError:
                pass
            continue

        m = re.search(r'Parallel steps detected:\s*(\d+)', line)
        if m:
            try:
                parallel_steps_detected = int(m.group(1))
            except ValueError:
                pass
            continue

    # Validate required fields
    if size is None or files is None or complexity is None:
        return None
    if optimistic_cost is None or pessimistic_cost is None:
        return None
    if expected_cost is None or expected_cost <= 0:
        return None

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "size": size,
        "files": files,
        "complexity": complexity,
        "project_type": project_type,
        "language": language,
        "steps": steps,
        "step_count": len(steps),
        "expected_cost": expected_cost,
        "optimistic_cost": optimistic_cost,
        "pessimistic_cost": pessimistic_cost,
        "baseline_cost": baseline_cost,
        "review_cycles_estimated": review_cycles_estimated,
        "review_cycles_actual": None,
        "parallel_groups": [],
        "parallel_steps_detected": parallel_steps_detected,
        "file_brackets": None,
        "files_measured": 0,
        "step_costs": {},
        "continuation": True,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parse_last_estimate.py <path-to-last-estimate.md>", file=sys.stderr)
        sys.exit(1)
    md_path = sys.argv[1]
    if not os.path.exists(md_path):
        sys.exit(1)
    max_age = float(os.environ.get("TOKENCOSTSCOPE_CONTINUATION_MAX_AGE_HOURS", "48"))
    try:
        with open(md_path) as f:
            content = f.read()
        mtime = os.path.getmtime(md_path)
        result = parse(content, max_age, mtime)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if result is None:
        sys.exit(1)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(0)
