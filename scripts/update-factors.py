#!/usr/bin/env python3
"""Compute calibration factors from estimate-vs-actual history.

Usage: python3 update-factors.py <history_jsonl_path> <factors_json_path>

Reads paired records from history.jsonl, filters outliers, computes
trimmed_mean(actual/expected) as the calibration factor per size class,
and writes factors.json.

Uses trimmed mean for first 10 samples, then EWMA for recency weighting.
Minimum 3 samples before a factor activates for any stratum.
Records with ratio >3.0 or <0.2 are flagged as outliers and excluded.
"""

import json
import sys
import tempfile
import os
from pathlib import Path

OUTLIER_HIGH = 3.0
OUTLIER_LOW = 0.2


def compute_ewma(values: list[float], alpha: float = 0.15) -> float:
    """Exponentially weighted moving average. Most recent values weighted highest."""
    if not values:
        return 1.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result


def trimmed_mean(values: list[float], trim_fraction: float = 0.1) -> float:
    """Mean after trimming extreme values. For N<10, k=0 (plain mean)."""
    if not values:
        return 1.0
    n = len(values)
    k = int(n * trim_fraction)
    sorted_vals = sorted(values)
    trimmed = sorted_vals[k : n - k] if k > 0 else sorted_vals
    return sum(trimmed) / len(trimmed)


def update_factors(history_path: str, factors_path: str) -> None:
    if not Path(history_path).exists():
        return

    # Pass 1: Collect all valid records
    all_records: list[dict] = []
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

            record["_ratio"] = actual / expected
            all_records.append(record)

    total_records = len(all_records)

    # Sort by timestamp for correct EWMA recency weighting
    all_records.sort(key=lambda r: r.get("timestamp", ""))

    # Pass 2: Separate outliers
    clean_records: list[dict] = []
    outliers: list[dict] = []
    for record in all_records:
        ratio = record["_ratio"]
        if ratio > OUTLIER_HIGH or ratio < OUTLIER_LOW:
            outliers.append({
                "timestamp": record.get("timestamp", ""),
                "size": record.get("size", ""),
                "ratio": round(ratio, 4),
                "expected_cost": record.get("expected_cost", 0),
                "actual_cost": record.get("actual_cost", 0),
            })
            print(
                f"Outlier excluded: ratio={ratio:.4f} size={record.get('size', '?')} "
                f"ts={record.get('timestamp', '?')}",
                file=sys.stderr,
            )
        else:
            clean_records.append(record)

    # Pass 3: Build ratio lists from clean records
    ratios_by_size: dict[str, list[float]] = {}
    all_ratios: list[float] = []
    for record in clean_records:
        ratio = record["_ratio"]
        size = record.get("size", "M")
        ratios_by_size.setdefault(size, []).append(ratio)
        all_ratios.append(ratio)

    sample_count = len(all_ratios)

    # Cap stored outliers to most recent 20 (count reflects all)
    outlier_count = len(outliers)
    stored_outliers = outliers[-20:]

    if sample_count < 3:
        factors: dict = {
            "sample_count": sample_count,
            "total_records": total_records,
            "outlier_count": outlier_count,
            "outliers": stored_outliers,
            "status": "collecting",
        }
        _write_atomic(factors_path, factors)
        return

    # Compute global factor
    if sample_count <= 10:
        global_factor = trimmed_mean(all_ratios)
    else:
        global_factor = compute_ewma(all_ratios)

    factors = {
        "sample_count": sample_count,
        "total_records": total_records,
        "outlier_count": outlier_count,
        "outliers": stored_outliers,
        "status": "active",
        "global": round(global_factor, 4),
    }

    # Per-size factors (only if 3+ samples in that stratum)
    for size, ratios in ratios_by_size.items():
        if len(ratios) >= 3:
            if len(ratios) <= 10:
                factor = trimmed_mean(ratios)
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
