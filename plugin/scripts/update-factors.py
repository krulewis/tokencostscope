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
import math
from datetime import datetime, timezone
from pathlib import Path

OUTLIER_HIGH = 3.0
OUTLIER_LOW = 0.2

DECAY_HALFLIFE_DAYS = 30   # mirrors decay_halflife_days in references/heuristics.md
                           # Update both together if changed.
DECAY_MIN_RECORDS = 5      # Cold-start guard: below this record count per stratum,
                           # decay is not applied (all weights = 1.0).
                           # Intentionally NOT in heuristics.md — this is a statistical
                           # invariant (prevents pathological early down-weighting), not
                           # a user-tunable parameter. See calibration-algorithm.md.


def compute_ewma(values: list[float], alpha: float = 0.15, weights=None) -> float:
    """Exponentially weighted moving average. Most recent values weighted highest.

    When weights are provided, the weight modulates the effective learning rate:
    eff_alpha = alpha * w; result = eff_alpha * v + (1 - eff_alpha) * result.

    The seed (first value) is NOT multiplied by its weight. Weights participate
    only in the iterative update, not in the initial seed. This prevents the seed
    from being artificially deflated when the oldest record is stale.

    This formulation is unbiased: for any constant sequence v=k, the result
    converges to k regardless of weight magnitude.
    """
    if not values:
        return 1.0
    # Seed is unweighted — weights only participate in iterative update.
    result = values[0]
    for i, v in enumerate(values[1:], 1):
        w = weights[i] if weights is not None else 1.0
        # Weight modulates the learning rate (effective alpha), not the sample value.
        # This ensures EWMA is unbiased: for any constant sequence v=k, the result
        # converges to k regardless of weight magnitude. Old records barely move the
        # result (tiny eff_alpha); recent records update at full alpha.
        eff_alpha = alpha * w
        result = eff_alpha * v + (1 - eff_alpha) * result
    return result


def trimmed_mean(values: list[float], trim_fraction: float = 0.1, weights=None) -> float:
    """Mean after trimming extreme values. For N<10, k=0 (plain mean).

    When weights are provided, trimming is by value (not by weight), then the
    trimmed set is reduced to a weighted mean: sum(v*w) / sum(w).

    Note: weights approach zero for very old records (float64 floor ~5e-324) but
    never reach exactly zero. In practice, records older than ~3500 days (halflife=30)
    are effectively zero-weighted but never cause division by zero since total_weight
    uses the sum of all trimmed weights.
    """
    if not values:
        return 1.0
    n = len(values)
    k = int(n * trim_fraction)
    if weights is None:
        weights = [1.0] * n
    # Sort by value (for trimming), keeping weights aligned
    paired = sorted(zip(values, weights), key=lambda x: x[0])
    trimmed = paired[k: n - k] if k > 0 else paired
    total_weight = sum(w for _, w in trimmed)
    if total_weight == 0:
        return 1.0
    return sum(v * w for v, w in trimmed) / total_weight


def compute_decay_weights(records: list[dict], halflife_days: float) -> list[float]:
    """Compute exponential time-decay weights for a list of records.

    w(record) = exp(-ln(2) / halflife_days * days_elapsed)

    where ln(2) is the natural log of 2, approximately 0.693.

    Returns weights in the same order as records.
    Records without a parseable timestamp receive weight 1.0 (no penalty).
    If len(records) <= DECAY_MIN_RECORDS, returns all-ones (cold-start guard:
    not enough data to safely down-weight early records).
    """
    if len(records) <= DECAY_MIN_RECORDS:
        return [1.0] * len(records)

    now = datetime.now(timezone.utc)
    weights = []
    for record in records:
        ts_str = record.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            days = (now - ts).total_seconds() / 86400.0
            w = math.exp(-math.log(2) / halflife_days * days)
        except (ValueError, TypeError):
            w = 1.0
        weights.append(w)
    # Normalize so most-recent record always has weight 1.0.
    # Prevents downward bias for infrequent users (>24h session gaps).
    max_w = max(weights)
    if max_w > 0:
        weights = [w / max_w for w in weights]
    return weights


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

            # Skip records explicitly marked as excluded.
            # string 'true' is truthy — IS excluded. Users should use JSON boolean true.
            if record.get('excluded', False):
                continue

            expected = record.get("expected_cost", 0)
            actual = record.get("actual_cost", 0)
            if expected <= 0 or actual <= 0:
                continue

            record["_ratio"] = actual / expected

            # Normalize pipeline_signature at read time (handles legacy freetext values).
            # If a 'steps' array is present, re-derive the canonical form to ensure consistent
            # grouping regardless of what string was written by older versions of learn.sh.
            steps_arr = record.get("steps")
            if steps_arr is not None:
                record["_canonical_sig"] = '+'.join(
                    sorted(s.lower().replace(' ', '_') for s in steps_arr)
                )
            elif record.get("pipeline_signature"):
                record["_canonical_sig"] = record["pipeline_signature"]
            else:
                record["_canonical_sig"] = ""

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

    # Compute decay weights once for the full clean_records list, sorted by timestamp.
    # Index alignment invariant: decay_weights_all[i] corresponds to clean_records[i].
    # This alignment holds throughout Passes 3, 4, and 5 — do not re-sort clean_records
    # after this point.
    decay_weights_all = compute_decay_weights(clean_records, DECAY_HALFLIFE_DAYS)

    # Pass 3: Build ratio lists and aligned weight lists from clean records.
    ratios_by_size: dict[str, list[float]] = {}
    weights_by_size: dict[str, list[float]] = {}
    all_ratios: list[float] = []
    for i, record in enumerate(clean_records):
        ratio = record["_ratio"]
        size = record.get("size", "M")
        ratios_by_size.setdefault(size, []).append(ratio)
        weights_by_size.setdefault(size, []).append(decay_weights_all[i])
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
        global_factor = trimmed_mean(all_ratios, weights=decay_weights_all)
    else:
        global_factor = compute_ewma(all_ratios, weights=decay_weights_all)

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
            w = weights_by_size[size]
            if len(ratios) <= 10:
                factor = trimmed_mean(ratios, weights=w)
            else:
                factor = compute_ewma(ratios, weights=w)
            factors[size] = round(factor, 4)
            factors[f"{size}_n"] = len(ratios)

    # Pass 4: Per-step factors
    # Collect step ratios from clean records. Each clean record contributes its
    # session-level ratio to every step listed in step_ratios.
    # PR Review Loop is excluded (it has its own calibration path — per-band
    # independent factors in Step 3.5). Exclusion uses exact string matching
    # (case-sensitive) to match the key written by SKILL.md.
    PR_REVIEW_LOOP_KEY = 'PR Review Loop'
    ratios_by_step: dict[str, list[float]] = {}
    weights_by_step: dict[str, list[float]] = {}
    for i, record in enumerate(clean_records):
        step_ratios = record.get('step_ratios', {})
        for step_name, ratio in step_ratios.items():
            if step_name == PR_REVIEW_LOOP_KEY:
                continue
            if not isinstance(ratio, (int, float)):
                continue
            ratios_by_step.setdefault(step_name, []).append(ratio)
            weights_by_step.setdefault(step_name, []).append(decay_weights_all[i])

    # Compute per-step factors using same trimmed_mean / EWMA thresholds as size-class.
    # per_step_min_samples = 3 is hardcoded to match the existing size-class threshold
    # in Pass 3 (line ~139: if len(ratios) >= 3). Both thresholds should be updated
    # together if changed. The value is also documented in references/heuristics.md.
    per_step_min_samples = 3
    step_factors: dict[str, dict] = {}
    for step_name, ratios in ratios_by_step.items():
        n = len(ratios)
        w = weights_by_step[step_name]
        if n <= 10:
            factor = trimmed_mean(ratios, weights=w)
        else:
            factor = compute_ewma(ratios, weights=w)
        status = 'active' if n >= per_step_min_samples else 'collecting'
        step_factors[step_name] = {
            'factor': round(factor, 4),
            'n': n,
            'status': status,
        }

    if step_factors:
        factors['step_factors'] = step_factors

    # Pass 5: Per-signature factors
    # _canonical_sig was normalized in Pass 1 at read time. We do not re-normalize here.
    per_signature_min_samples = 3   # mirrors heuristics.md per_signature_min_samples
                                     # Matches per-step and size-class thresholds.
    ratios_by_sig: dict[str, list[float]] = {}
    weights_by_sig: dict[str, list[float]] = {}
    for i, record in enumerate(clean_records):
        sig = record.get("_canonical_sig", "")
        if not sig:
            continue
        ratios_by_sig.setdefault(sig, []).append(record['_ratio'])
        weights_by_sig.setdefault(sig, []).append(decay_weights_all[i])

    signature_factors: dict[str, dict] = {}
    for sig, ratios in ratios_by_sig.items():
        n = len(ratios)
        w = weights_by_sig[sig]
        if n <= 10:
            factor = trimmed_mean(ratios, weights=w)
        else:
            factor = compute_ewma(ratios, weights=w)
        status = 'active' if n >= per_signature_min_samples else 'collecting'
        signature_factors[sig] = {'factor': round(factor, 4), 'n': n, 'status': status}

    if signature_factors:
        factors['signature_factors'] = signature_factors

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

    # v1.6.0: time-decay weighting (Item A), per-signature factors Pass 5 (Item B)
    update_factors(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
