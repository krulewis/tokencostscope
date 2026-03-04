#!/usr/bin/env python3
"""Compute calibration factors from estimate-vs-actual history.

Usage: python3 update-factors.py <history_jsonl_path> <factors_json_path>

Reads paired records from history.jsonl, computes median(actual/expected)
as the calibration factor per size class, and writes factors.json.

Uses simple median for first 10 samples, then EWMA for recency weighting.
Minimum 3 samples before a factor activates for any stratum.
"""

import json
import sys
import tempfile
import os
from pathlib import Path
from statistics import median


def compute_ewma(values: list[float], alpha: float = 0.15) -> float:
    """Exponentially weighted moving average. Most recent values weighted highest."""
    if not values:
        return 1.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result


def update_factors(history_path: str, factors_path: str) -> None:
    ratios_by_size: dict[str, list[float]] = {}
    all_ratios: list[float] = []
    sample_count = 0

    if not Path(history_path).exists():
        return

    with open(history_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            expected = record.get("expected_cost", 0)
            actual = record.get("actual_cost", 0)
            if expected <= 0 or actual <= 0:
                continue

            ratio = actual / expected
            size = record.get("size", "M")
            ratios_by_size.setdefault(size, []).append(ratio)
            all_ratios.append(ratio)
            sample_count += 1

    if sample_count < 3:
        # Not enough data — write empty factors
        factors = {"sample_count": sample_count, "status": "collecting"}
        _write_atomic(factors_path, factors)
        return

    # Compute global factor
    if sample_count <= 10:
        global_factor = median(all_ratios)
    else:
        global_factor = compute_ewma(all_ratios)

    factors: dict = {
        "sample_count": sample_count,
        "status": "active",
        "global": round(global_factor, 4),
    }

    # Per-size factors (only if 3+ samples in that stratum)
    for size, ratios in ratios_by_size.items():
        if len(ratios) >= 3:
            if len(ratios) <= 10:
                factor = median(ratios)
            else:
                factor = compute_ewma(ratios)
            factors[size] = round(factor, 4)
            factors[f"{size}_n"] = len(ratios)

    _write_atomic(factors_path, factors)


def _write_atomic(path: str, data: dict) -> None:
    """Write JSON atomically via temp file + rename."""
    dir_path = os.path.dirname(path) or "."
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        os.unlink(tmp_path)
        raise


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: update-factors.py <history_jsonl> <factors_json>",
            file=sys.stderr,
        )
        sys.exit(1)

    update_factors(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
