"""File measurement utility for tokencast.

Handles wc -l subprocess calls, bracket assignment, and weighted-average
token computation. Separated from estimation_engine.py so MCP tool callers
can invoke it independently without running a full estimate.
"""

import subprocess
from pathlib import Path

from tokencast.heuristics import FILE_SIZE_BRACKETS


def assign_bracket(line_count: int) -> str:
    """Map a line count to a bracket name.

    Returns "small", "medium", or "large" based on FILE_SIZE_BRACKETS boundaries.
    """
    small_max = FILE_SIZE_BRACKETS["small_max_lines"]   # 49
    large_min = FILE_SIZE_BRACKETS["large_min_lines"]   # 501

    if line_count <= small_max:
        return "small"
    if line_count >= large_min:
        return "large"
    return "medium"


def bracket_from_override(avg_file_lines: int) -> str:
    """Map an avg_file_lines integer override to a bracket name.

    Returns "small", "medium", or "large" per heuristics.md boundaries.
    """
    return assign_bracket(avg_file_lines)


def compute_avg_tokens(brackets: dict) -> tuple:
    """Given a brackets dict, return (avg_file_read_tokens, avg_file_edit_tokens).

    Formula:
        avg_read = (small×3000 + medium×10000 + large×20000) / total_measured
        avg_edit = (small×1000 + medium×2500  + large×5000)  / total_measured

    Zero-divide guard: if total_measured == 0, return (10000, 2500).
    """
    bk = FILE_SIZE_BRACKETS["brackets"]
    small_count  = brackets.get("small",  0)
    medium_count = brackets.get("medium", 0)
    large_count  = brackets.get("large",  0)
    total = small_count + medium_count + large_count

    if total == 0:
        return (10000, 2500)

    avg_read = (
        small_count  * bk["small"]["file_read_input"]
        + medium_count * bk["medium"]["file_read_input"]
        + large_count  * bk["large"]["file_read_input"]
    ) / total

    avg_edit = (
        small_count  * bk["small"]["file_edit_input"]
        + medium_count * bk["medium"]["file_edit_input"]
        + large_count  * bk["large"]["file_edit_input"]
    ) / total

    return (avg_read, avg_edit)


def compute_bracket_tokens_from_override(avg_file_lines: int) -> dict:
    """Return {"file_read_input": N, "file_edit_input": N} for the bracket
    that avg_file_lines maps to.

    Used for the fallback case when no file_paths are provided but avg_file_lines
    is given.
    """
    bracket_name = bracket_from_override(avg_file_lines)
    bk = FILE_SIZE_BRACKETS["brackets"][bracket_name]
    return {
        "file_read_input": bk["file_read_input"],
        "file_edit_input": bk["file_edit_input"],
    }


def measure_files(file_paths: list, project_dir: str = None) -> dict:
    """Measure file sizes via wc -l, assign brackets, compute weighted averages.

    Args:
        file_paths: List of file path strings to measure.
        project_dir: Optional project directory for resolving relative paths.

    Returns:
        Dict with keys:
            - "brackets": {"small": N, "medium": N, "large": N} or None
            - "files_measured": int
            - "avg_file_read_tokens": float
            - "avg_file_edit_tokens": float

    When file_paths is empty, returns brackets=None (no paths extracted).
    When paths exist but none are measurable, returns brackets={"small":0,"medium":0,"large":0}.
    On subprocess failure, returns brackets=None with default token values.
    """
    _default_result = {
        "brackets": None,
        "files_measured": 0,
        "avg_file_read_tokens": 10000,
        "avg_file_edit_tokens": 2500,
    }

    if not file_paths:
        return _default_result

    binary_extensions = set(FILE_SIZE_BRACKETS["binary_extensions"])
    cap = FILE_SIZE_BRACKETS["measurement_cap"]  # 30

    # Filter binary extensions
    measurable_paths = []
    for p in file_paths:
        ext = Path(p).suffix.lower()
        if ext not in binary_extensions:
            measurable_paths.append(p)

    # If all paths are binary, return zero-count brackets
    if not measurable_paths:
        return {
            "brackets": {"small": 0, "medium": 0, "large": 0},
            "files_measured": 0,
            "avg_file_read_tokens": 10000,
            "avg_file_edit_tokens": 2500,
        }

    # Resolve relative paths against project_dir
    if project_dir:
        resolved_paths = []
        for p in measurable_paths:
            path_obj = Path(p)
            if not path_obj.is_absolute():
                path_obj = Path(project_dir) / path_obj
            resolved_paths.append(str(path_obj))
    else:
        resolved_paths = list(measurable_paths)

    # Cap at 30 files; track overflow paths for bracket assignment
    capped_paths = resolved_paths[:cap]
    overflow_paths = resolved_paths[cap:]

    try:
        proc = subprocess.run(
            ["wc", "-l", "--"] + capped_paths,
            capture_output=True,
            text=True,
        )
        stdout = proc.stdout
    except Exception:
        # Subprocess failure — return null brackets with defaults
        return _default_result

    # Parse wc -l output
    # macOS format: "  <count> <path>" with "  <total> total" as last line (multi-file)
    brackets = {"small": 0, "medium": 0, "large": 0}
    files_measured = 0

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        # Skip the "total" summary line
        if parts[1] == "total" or (len(parts) >= 2 and parts[-1] == "total"):
            continue
        try:
            count = int(parts[0])
        except ValueError:
            continue
        bracket = assign_bracket(count)
        brackets[bracket] += 1
        files_measured += 1

    if files_measured == 0 and not overflow_paths:
        # Paths were provided but none were measurable
        return {
            "brackets": {"small": 0, "medium": 0, "large": 0},
            "files_measured": 0,
            "avg_file_read_tokens": 10000,
            "avg_file_edit_tokens": 2500,
        }

    # Handle overflow: assign weighted-average bracket of first 30 measured
    if overflow_paths:
        avg_read, avg_edit = compute_avg_tokens(brackets)
        # Assign overflow files to the bracket implied by avg_file_read_tokens
        # Use the first 30's average bracket to count extras
        for _ in overflow_paths:
            if avg_read <= 3000:
                brackets["small"] += 1
            elif avg_read <= 10000:
                brackets["medium"] += 1
            else:
                brackets["large"] += 1

    avg_read, avg_edit = compute_avg_tokens(brackets)

    return {
        "brackets": brackets,
        "files_measured": files_measured,
        "avg_file_read_tokens": avg_read,
        "avg_file_edit_tokens": avg_edit,
    }
