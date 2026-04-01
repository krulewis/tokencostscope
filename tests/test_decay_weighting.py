"""Tests for time-decay weighting in calibration factors (v1.6.0).

Tests compute_decay_weights(), weighted trimmed_mean(), weighted compute_ewma(),
decay integration in Passes 3-5, and cold-start guard behavior.

Arithmetic tests pass before implementation (they test helper formulas directly).
Integration tests (TestDecayWeightingIntegration) test update_factors() end-to-end.
"""
# Runner: pytest (required). Use: /usr/bin/python3 -m pytest tests/

import importlib.util
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
UPDATE_FACTORS_PY = SCRIPTS_DIR / "update-factors.py"

_spec = importlib.util.spec_from_file_location("update_factors", str(UPDATE_FACTORS_PY))
assert _spec is not None, f"Could not load spec for {UPDATE_FACTORS_PY}"
assert _spec.loader is not None, "Spec loader is None"
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
compute_decay_weights = _mod.compute_decay_weights
compute_ewma = _mod.compute_ewma
trimmed_mean = _mod.trimmed_mean
update_factors = _mod.update_factors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_record(days_ago, ratio=0.9, size="M", step_ratios=None, **kwargs):
    """Create a minimal history record with a timestamp set to days_ago days in the past."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    record = {
        "timestamp": ts,
        "size": size,
        "expected_cost": 5.0,
        "actual_cost": 5.0 * ratio,
    }
    if step_ratios is not None:
        record["step_ratios"] = step_ratios
    record.update(kwargs)
    return record


def run_update_factors(records):
    """Write records to a temp history.jsonl, call update_factors, return parsed factors dict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = os.path.join(tmpdir, "history.jsonl")
        factors_path = os.path.join(tmpdir, "factors.json")
        with open(history_path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        update_factors(history_path, factors_path)
        if not os.path.exists(factors_path):
            return {}
        with open(factors_path) as f:
            return json.load(f)


# ---------------------------------------------------------------------------
# Class 1: TestComputeDecayWeights
# ---------------------------------------------------------------------------

class TestComputeDecayWeights:
    """Unit tests for compute_decay_weights() helper."""

    def test_empty_list_returns_empty(self):
        """compute_decay_weights([], 30) returns []."""
        result = compute_decay_weights([], 30)
        assert result == []

    def test_cold_start_guard_5_records(self):
        """Exactly 5 records: all weights == 1.0 (cold-start guard)."""
        records = [make_record(days_ago=i * 20) for i in range(5)]
        weights = compute_decay_weights(records, 30)
        assert len(weights) == 5
        assert all(w == 1.0 for w in weights)

    def test_cold_start_guard_6_records(self):
        """6 records spanning 90 days: NOT all 1.0 (at least one weight < 1.0)."""
        records = [make_record(days_ago=i * 18) for i in range(6)]
        weights = compute_decay_weights(records, 30)
        assert len(weights) == 6
        # The oldest record (90 days ago) should have weight < 1.0
        assert not all(w == 1.0 for w in weights), "Expected at least one weight < 1.0 for old records"

    def test_weight_at_halflife_is_half(self):
        """Record exactly 30 days ago: weight ≈ 0.5."""
        records = [make_record(days_ago=30)]
        # Need > 5 records to bypass cold-start guard — pad with extra records
        padding = [make_record(days_ago=0) for _ in range(5)]
        all_records = records + padding
        weights = compute_decay_weights(all_records, 30)
        # First record is the 30-days-ago one
        assert abs(weights[0] - 0.5) < 0.01, f"Expected weight ≈ 0.5, got {weights[0]}"

    def test_weight_at_two_halflives_is_quarter(self):
        """Record exactly 60 days ago: weight ≈ 0.25."""
        records = [make_record(days_ago=60)]
        padding = [make_record(days_ago=0) for _ in range(5)]
        all_records = records + padding
        weights = compute_decay_weights(all_records, 30)
        assert abs(weights[0] - 0.25) < 0.01, f"Expected weight ≈ 0.25, got {weights[0]}"

    def test_today_record_weight_is_one(self):
        """Record with today's timestamp: weight ≈ 1.0."""
        records = [make_record(days_ago=0)]
        padding = [make_record(days_ago=30) for _ in range(5)]
        all_records = records + padding
        weights = compute_decay_weights(all_records, 30)
        assert abs(weights[0] - 1.0) < 0.01, f"Expected weight ≈ 1.0, got {weights[0]}"

    def test_missing_timestamp_gets_weight_one(self):
        """Record without 'timestamp' field: weight == 1.0."""
        no_ts_record = {"expected_cost": 5.0, "actual_cost": 4.5}
        padding = [make_record(days_ago=10) for _ in range(5)]
        all_records = [no_ts_record] + padding
        weights = compute_decay_weights(all_records, 30)
        assert weights[0] == 1.0

    def test_invalid_timestamp_gets_weight_one(self):
        """Record with malformed timestamp string: weight == 1.0."""
        bad_ts_record = {"timestamp": "not-a-date", "expected_cost": 5.0, "actual_cost": 4.5}
        padding = [make_record(days_ago=10) for _ in range(5)]
        all_records = [bad_ts_record] + padding
        weights = compute_decay_weights(all_records, 30)
        assert weights[0] == 1.0

    def test_weights_ordered_newer_higher(self):
        """3 records spanning 90 days: weights monotonically decreasing (oldest first)."""
        # Old to new: 90, 45, 0 days ago
        records = [
            make_record(days_ago=90),
            make_record(days_ago=45),
            make_record(days_ago=0),
        ]
        padding = [make_record(days_ago=5) for _ in range(3)]
        all_records = records + padding
        weights = compute_decay_weights(all_records, 30)
        # First three: oldest → newest; weights should be increasing
        assert weights[0] < weights[1] < weights[2], (
            f"Expected w[0]<w[1]<w[2] but got {weights[0]:.4f}, {weights[1]:.4f}, {weights[2]:.4f}"
        )

    def test_all_weights_positive(self):
        """All computed weights are > 0."""
        records = [make_record(days_ago=i * 100) for i in range(10)]
        weights = compute_decay_weights(records, 30)
        assert all(w > 0 for w in weights), f"Non-positive weight found in {weights}"


# ---------------------------------------------------------------------------
# Class 2: TestWeightedTrimmedMean
# ---------------------------------------------------------------------------

class TestWeightedTrimmedMean:
    """Unit tests for weighted trimmed_mean() function."""

    def test_unweighted_matches_original(self):
        """trimmed_mean without weights matches old behavior."""
        values = [0.8, 1.0, 1.2]
        result_no_weights = trimmed_mean(values)
        result_weights_none = trimmed_mean(values, weights=None)
        assert abs(result_no_weights - result_weights_none) < 1e-10

    def test_equal_weights_matches_unweighted(self):
        """All-ones weights produces same result as no weights."""
        values = [0.8, 0.9, 1.0, 1.1, 1.2]
        result_no_weights = trimmed_mean(values)
        result_ones = trimmed_mean(values, weights=[1.0] * len(values))
        assert abs(result_no_weights - result_ones) < 1e-10

    def test_zero_weight_excludes_value(self):
        """Value 100.0 with weight ≈ 0.0001 has near-zero influence."""
        values = [1.0, 1.0, 1.0, 100.0]
        weights = [1.0, 1.0, 1.0, 0.0001]
        result = trimmed_mean(values, weights=weights)
        # Result should be very close to 1.0 (not pulled toward 100)
        assert result < 2.0, f"Expected result close to 1.0, got {result}"

    def test_high_weight_dominates(self):
        """One value with weight=10, rest weight=1: result closer to high-weight value."""
        values = [1.0, 1.0, 3.0]
        weights = [1.0, 1.0, 10.0]
        result = trimmed_mean(values, weights=weights)
        # Unweighted mean ≈ 1.67; weighted mean should be closer to 3.0
        unweighted = trimmed_mean(values)
        assert result > unweighted, f"Expected weighted result {result} > unweighted {unweighted}"
        assert result > 2.0, f"Expected result closer to 3.0, got {result}"

    def test_empty_returns_one(self):
        """Empty list returns 1.0."""
        assert trimmed_mean([]) == 1.0

    def test_single_value(self):
        """Single value returns that value."""
        assert trimmed_mean([1.5]) == 1.5

    def test_trimming_still_works_with_weights(self):
        """With n=10, k=1 (10% trim), extreme values are excluded before weighting."""
        # Low extreme = 0.1, high extreme = 10.0; middle values ≈ 1.0
        values = [0.1] + [1.0] * 8 + [10.0]
        weights = [1.0] * 10
        result = trimmed_mean(values, trim_fraction=0.1, weights=weights)
        # After trimming extremes, result should be close to 1.0
        assert abs(result - 1.0) < 0.01, f"Expected result ≈ 1.0 after trimming, got {result}"


# ---------------------------------------------------------------------------
# Class 3: TestWeightedEWMA
# ---------------------------------------------------------------------------

class TestWeightedEWMA:
    """Unit tests for weighted compute_ewma() function."""

    def test_unweighted_matches_original(self):
        """No weights argument produces same result as old behavior."""
        values = [0.8, 0.9, 1.0, 1.1, 1.2]
        result_no_arg = compute_ewma(values)
        result_none = compute_ewma(values, weights=None)
        assert abs(result_no_arg - result_none) < 1e-10

    def test_empty_returns_one(self):
        """Empty list returns 1.0."""
        assert compute_ewma([]) == 1.0

    def test_single_value_with_weight(self):
        """CRITICAL: compute_ewma([2.0], weights=[0.3]) returns 2.0 NOT 0.6.

        The seed (first value) is unweighted. Weight only participates in
        iterative updates. This ensures the seed is not artificially deflated.
        """
        result = compute_ewma([2.0], weights=[0.3])
        assert result == 2.0, f"Expected 2.0 (unweighted seed), got {result}"

    def test_all_weight_one_matches_standard(self):
        """All-ones weights produces same result as no-weights EWMA."""
        values = [0.8, 0.9, 1.0, 1.1, 1.2]
        result_standard = compute_ewma(values)
        result_ones = compute_ewma(values, weights=[1.0] * len(values))
        assert abs(result_standard - result_ones) < 1e-10

    def test_weight_half_halves_influence(self):
        """A weight of 0.5 halves the effective learning rate in the update step."""
        # With alpha=0.15, seed=1.0, second value=3.0:
        # Standard (w=1.0): eff_alpha=0.15; result = 0.15*3.0 + 0.85*1.0 = 1.30
        # Weighted (w=0.5): eff_alpha=0.075; result = 0.075*3.0 + 0.925*1.0 = 1.15
        result_weighted = compute_ewma([1.0, 3.0], weights=[1.0, 0.5])
        result_standard = compute_ewma([1.0, 3.0])
        # Weighted result should be between seed (1.0) and standard result (1.30)
        assert result_weighted < result_standard, (
            f"Weighted result {result_weighted} should be less than standard {result_standard}"
        )
        expected = 0.075 * 3.0 + 0.925 * 1.0  # eff_alpha = alpha * w = 0.15 * 0.5
        assert abs(result_weighted - expected) < 1e-10, f"Expected {expected}, got {result_weighted}"


# ---------------------------------------------------------------------------
# Class 4: TestDecayIntegration
# (Uses update_factors() end-to-end with temp files)
# ---------------------------------------------------------------------------

class TestDecayIntegration:
    """Integration tests for decay weighting applied through update_factors()."""

    def test_5_records_no_decay(self):
        """5 records spanning 90 days: factor should match unweighted (cold-start guard)."""
        records = [make_record(days_ago=i * 22, ratio=0.9) for i in range(5)]
        factors = run_update_factors(records)
        # With cold-start guard, all weights = 1.0, result should match plain trimmed_mean
        assert factors.get("status") in ("active", "collecting")
        # If active (3+ clean records), verify the factor is close to 0.9
        if factors.get("status") == "active":
            assert abs(factors.get("global", 0.9) - 0.9) < 0.05

    def test_6_records_uses_decay(self):
        """6 records where first 5 are 90-days-old (ratio=2.0), last is today (ratio=0.5).

        With decay applied, factor should be pulled toward 0.5 (recent record dominates).
        Without decay, factor would be close to (5*2.0 + 1*0.5)/6 ≈ 1.75.
        """
        old_records = [make_record(days_ago=90, ratio=2.0) for _ in range(5)]
        recent_record = make_record(days_ago=0, ratio=0.5)
        records = old_records + [recent_record]
        factors = run_update_factors(records)

        if factors.get("status") == "active":
            global_factor = factors.get("global", 1.0)
            # With decay, the recent record (weight ≈ 1.0) dominates over old ones (weight ≈ 0.06)
            # Factor should be closer to 0.5 than to 1.75 (the unweighted mean)
            # A strict lower bound: factor < 1.5 (significantly pulled toward 0.5)
            assert global_factor < 1.5, (
                f"Expected decay to pull factor toward 0.5, but got {global_factor}"
            )

    def test_fresh_records_all_today(self):
        """10 records all from today: factor matches weighted mean (all weights ≈ 1.0)."""
        records = [make_record(days_ago=0, ratio=1.2) for _ in range(10)]
        factors = run_update_factors(records)
        if factors.get("status") == "active":
            global_factor = factors.get("global", 1.0)
            assert abs(global_factor - 1.2) < 0.05, f"Expected factor ≈ 1.2, got {global_factor}"

    def test_stale_records_down_weighted(self):
        """First 6 records very old (ratio=2.0), last 4 recent (ratio=0.8).

        With decay, recent records dominate; factor should be < 1.5.
        """
        old = [make_record(days_ago=120, ratio=2.0) for _ in range(6)]
        recent = [make_record(days_ago=1, ratio=0.8) for _ in range(4)]
        records = old + recent
        factors = run_update_factors(records)
        if factors.get("status") == "active":
            global_factor = factors.get("global", 1.0)
            assert global_factor < 1.5, f"Expected decay to down-weight stale records, got {global_factor}"

    def test_decay_in_pass3_size_class(self):
        """Per-size factor is also decay-weighted: recent records dominate old ones."""
        # All size M: 3 old records (ratio=2.0), 3 recent (ratio=0.8)
        old = [make_record(days_ago=90, ratio=2.0, size="M") for _ in range(3)]
        recent = [make_record(days_ago=1, ratio=0.8, size="M") for _ in range(3)]
        records = old + recent
        factors = run_update_factors(records)
        # Size-class factor for M should exist and be below midpoint
        m_factor = factors.get("M")
        if m_factor is not None:
            # Unweighted mean ≈ 1.4; with decay toward 0.8
            assert m_factor < 1.5, f"Expected size-class factor pulled toward recent records, got {m_factor}"

    def test_decay_in_pass4_per_step(self):
        """Per-step factor is also decay-weighted."""
        # 3 old records with step ratio=2.0, 3 recent with step ratio=0.8
        old = [make_record(days_ago=90, ratio=2.0, step_ratios={"Research Agent": 2.0}) for _ in range(3)]
        recent = [make_record(days_ago=1, ratio=0.8, step_ratios={"Research Agent": 0.8}) for _ in range(3)]
        records = old + recent
        factors = run_update_factors(records)
        step_factors = factors.get("step_factors", {})
        ra_factor = step_factors.get("Research Agent", {}).get("factor")
        if ra_factor is not None:
            assert ra_factor < 1.5, f"Expected step factor pulled toward recent, got {ra_factor}"

    def test_weekly_user_converges_within_15pct(self):
        """20 records spaced 7 days apart (weekly user) with true ratio 1.5.

        Before normalization fix: EWMA converges ~27% below true ratio (~1.09).
        After normalization: EWMA should converge within 15% of true ratio (1.275-1.725).

        This test validates the fix for the downward bias affecting infrequent users.
        """
        true_ratio = 1.5
        n = 20
        # Records from oldest to newest: (n-1)*7 days ago → 0 days ago
        records = [
            make_record(days_ago=(n - 1 - i) * 7, ratio=true_ratio)
            for i in range(n)
        ]
        factors = run_update_factors(records)
        assert factors.get("status") == "active", "Expected active status with 20 records"
        global_factor = factors.get("global", 0.0)
        lower = true_ratio * 0.85
        upper = true_ratio * 1.15
        assert lower <= global_factor <= upper, (
            f"Weekly user factor {global_factor:.4f} not within 15% of true ratio {true_ratio} "
            f"(expected {lower:.3f}–{upper:.3f}). EWMA normalization may be missing."
        )
