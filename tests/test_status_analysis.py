# Run with: /usr/bin/python3 -m pytest tests/test_status_analysis.py
"""Tests for tokencostscope-status.py (v2.0 Change 11).

Covers:
- get_ratio: stored vs calculated vs zero-division guard
- band_hit: stored cost boundaries vs ratio fallback
- compute_health: no_data/collecting/active, factor level detection
- compute_accuracy: mean/median, trend detection, band hit rate
- compute_cost_attribution: step aggregation, sorting, missing data
- compute_outliers: high/low ratio detection, excluded records
- compute_recommendations: review cycles, band width, outlier rate, step dominance
- resolve_window: adaptive, count spec, all, empty
- sparse behavior: no history, 1 record, all outliers, verbose bypass
- JSON output schema
- subprocess integration
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path('/Volumes/Macintosh HD2/Cowork/Projects/costscope')
STATUS_SCRIPT = REPO_ROOT / 'scripts' / 'tokencostscope-status.py'
HEURISTICS_PATH = str(REPO_ROOT / 'references' / 'heuristics.md')

# Constants mirrored from status.py spec
OUTLIER_HIGH = 3.0
OUTLIER_LOW = 0.2
DEFAULT_WINDOW_SESSIONS = 10
DEFAULT_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_record(ratio=1.2, size='M', timestamp=None, step_actuals=None,
                optimistic_cost=None, pessimistic_cost=None,
                review_cycles_actual=None, excluded=False) -> dict:
    """Minimal valid history record."""
    ts = timestamp or '2026-01-01T00:00:00Z'
    expected = 5.0
    actual = expected * ratio
    r = {
        'timestamp': ts, 'size': size, 'complexity': 'medium',
        'expected_cost': expected, 'actual_cost': actual, 'ratio': ratio,
        'project_type': 'greenfield', 'language': 'python',
        'steps': ['Implementation'], 'step_count': 1,
        'review_cycles_estimated': 2,
    }
    if optimistic_cost is not None:
        r['optimistic_cost'] = optimistic_cost
    if pessimistic_cost is not None:
        r['pessimistic_cost'] = pessimistic_cost
    if step_actuals is not None:
        r['step_actuals'] = step_actuals
    if review_cycles_actual is not None:
        r['review_cycles_actual'] = review_cycles_actual
    if excluded:
        r['excluded'] = True
    return r


def make_history_file(tmp_path, records) -> str:
    path = str(tmp_path / 'history.jsonl')
    with open(path, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')
    return path


def make_factors_file(tmp_path, factors=None) -> str:
    path = str(tmp_path / 'factors.json')
    with open(path, 'w') as f:
        json.dump(factors or {}, f)
    return path


def load_status_module():
    """Import tokencostscope-status.py via importlib (filename has hyphen)."""
    if not STATUS_SCRIPT.exists():
        pytest.skip(f'tokencostscope-status.py not found: {STATUS_SCRIPT}')
    spec = importlib.util.spec_from_file_location(
        'tokencostscope_status', str(STATUS_SCRIPT)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_status_script(*args) -> tuple:
    """Run the status script as a subprocess. Returns (returncode, stdout, stderr)."""
    cmd = [
        '/usr/bin/python3',
        str(STATUS_SCRIPT),
    ]
    cmd.extend(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# TestGetRatio
# ---------------------------------------------------------------------------

class TestGetRatio:
    def test_uses_stored_ratio(self, tmp_path):
        """Record with ratio=1.5 → get_ratio returns 1.5 (F14)."""
        mod = load_status_module()
        rec = make_record(ratio=1.5)
        assert mod.get_ratio(rec) == pytest.approx(1.5)

    def test_falls_back_to_calculation(self, tmp_path):
        """Record without ratio field → actual/expected used."""
        mod = load_status_module()
        rec = make_record(ratio=1.2)
        del rec['ratio']
        rec['expected_cost'] = 4.0
        rec['actual_cost'] = 6.0
        result = mod.get_ratio(rec)
        assert result == pytest.approx(1.5)

    def test_zero_expected_doesnt_crash(self, tmp_path):
        """expected=0 → no ZeroDivisionError."""
        mod = load_status_module()
        rec = make_record(ratio=1.0)
        del rec['ratio']
        rec['expected_cost'] = 0.0
        rec['actual_cost'] = 1.0
        # Should not raise — return some defined value (None, 0, or sentinel)
        try:
            result = mod.get_ratio(rec)
        except ZeroDivisionError:
            pytest.fail('get_ratio raised ZeroDivisionError on expected=0')


# ---------------------------------------------------------------------------
# TestBandHit
# ---------------------------------------------------------------------------

class TestBandHit:
    def test_within_optimistic_uses_stored(self, tmp_path):
        """actual <= optimistic_cost → 'optimistic' (F10)."""
        mod = load_status_module()
        rec = make_record(ratio=0.5, optimistic_cost=3.0, pessimistic_cost=15.0)
        # actual_cost = 5.0 * 0.5 = 2.5 <= optimistic_cost 3.0
        result = mod.band_hit(rec)
        assert result == 'optimistic'

    def test_within_expected_uses_stored(self, tmp_path):
        """actual > optimistic_cost but <= pessimistic_cost → 'expected' (F10)."""
        mod = load_status_module()
        rec = make_record(ratio=1.0, optimistic_cost=2.0, pessimistic_cost=15.0)
        # actual_cost = 5.0, > optimistic 2.0, <= pessimistic 15.0
        result = mod.band_hit(rec)
        assert result == 'expected'

    def test_over_pessimistic_uses_stored(self, tmp_path):
        """actual > pessimistic_cost → 'over_pessimistic' (F10)."""
        mod = load_status_module()
        rec = make_record(ratio=4.0, optimistic_cost=2.0, pessimistic_cost=10.0)
        # actual_cost = 5.0 * 4.0 = 20.0 > pessimistic 10.0
        result = mod.band_hit(rec)
        assert result == 'over_pessimistic'

    def test_fallback_ratio_based_optimistic(self, tmp_path):
        """No stored costs → ratio <= 0.6 → 'optimistic'."""
        mod = load_status_module()
        rec = make_record(ratio=0.5)
        result = mod.band_hit(rec)
        assert result == 'optimistic'

    def test_fallback_ratio_based_expected(self, tmp_path):
        """No stored costs → 0.6 < ratio <= 3.0 → 'expected'."""
        mod = load_status_module()
        rec = make_record(ratio=1.5)
        result = mod.band_hit(rec)
        assert result == 'expected'

    def test_fallback_ratio_based_over_pessimistic(self, tmp_path):
        """No stored costs → ratio > 3.0 → 'over_pessimistic'."""
        mod = load_status_module()
        rec = make_record(ratio=3.5)
        result = mod.band_hit(rec)
        assert result == 'over_pessimistic'


# ---------------------------------------------------------------------------
# TestHealthComputation
# ---------------------------------------------------------------------------

class TestHealthComputation:
    def test_no_data_status(self, tmp_path):
        """Empty history → health.status == 'no_data'."""
        mod = load_status_module()
        history_path = make_history_file(tmp_path, [])
        factors_path = make_factors_file(tmp_path)
        health = mod.compute_health([], mod.load_factors(factors_path))
        assert health['status'] == 'no_data'

    def test_collecting_status(self, tmp_path):
        """2 clean records → status 'collecting'."""
        mod = load_status_module()
        records = [make_record(ratio=1.1), make_record(ratio=0.9)]
        factors_path = make_factors_file(tmp_path)
        health = mod.compute_health(records, mod.load_factors(factors_path))
        assert health['status'] == 'collecting'

    def test_active_status(self, tmp_path):
        """3+ clean records → status 'active'."""
        mod = load_status_module()
        records = [make_record(ratio=1.1), make_record(ratio=0.9), make_record(ratio=1.2)]
        factors_path = make_factors_file(tmp_path)
        health = mod.compute_health(records, mod.load_factors(factors_path))
        assert health['status'] == 'active'

    def test_active_factor_level_per_step(self, tmp_path):
        """factors with step_factors entry having status='active' → active_factor_level 'per-step'."""
        mod = load_status_module()
        records = [make_record() for _ in range(3)]
        factors = {
            'step_factors': {
                'Implementation': {'factor': 0.9, 'n': 5, 'status': 'active'}
            }
        }
        factors_path = make_factors_file(tmp_path, factors)
        health = mod.compute_health(records, mod.load_factors(factors_path))
        assert health['active_factor_level'] == 'per-step'

    def test_active_factor_level_global(self, tmp_path):
        """factors with 'global' factor and status='active' → 'global'."""
        mod = load_status_module()
        records = [make_record() for _ in range(3)]
        factors = {
            'global': {'factor': 0.85, 'n': 10, 'status': 'active'}
        }
        factors_path = make_factors_file(tmp_path, factors)
        health = mod.compute_health(records, mod.load_factors(factors_path))
        assert health['active_factor_level'] == 'global'

    def test_active_factor_level_none(self, tmp_path):
        """Empty factors → 'none'."""
        mod = load_status_module()
        records = [make_record() for _ in range(3)]
        factors_path = make_factors_file(tmp_path, {})
        health = mod.compute_health(records, mod.load_factors(factors_path))
        assert health['active_factor_level'] == 'none'


# ---------------------------------------------------------------------------
# TestAccuracyComputation
# ---------------------------------------------------------------------------

class TestAccuracyComputation:
    def test_mean_and_median_ratio(self, tmp_path):
        """3 records with ratios 1.0, 1.5, 2.0 → mean=1.5, median=1.5."""
        mod = load_status_module()
        records = [
            make_record(ratio=1.0),
            make_record(ratio=1.5),
            make_record(ratio=2.0),
        ]
        acc = mod.compute_accuracy(records, False)
        assert acc['mean_ratio'] == pytest.approx(1.5)
        assert acc['median_ratio'] == pytest.approx(1.5)

    def test_trend_improving(self, tmp_path):
        """ratios [2.0, 1.5, 1.2, 1.1] → trend 'improving'."""
        mod = load_status_module()
        records = [
            make_record(ratio=2.0, timestamp='2026-01-01T00:00:00Z'),
            make_record(ratio=1.5, timestamp='2026-01-08T00:00:00Z'),
            make_record(ratio=1.2, timestamp='2026-01-15T00:00:00Z'),
            make_record(ratio=1.1, timestamp='2026-01-22T00:00:00Z'),
        ]
        acc = mod.compute_accuracy(records, False)
        assert acc['trend'] == 'improving'

    def test_trend_stable(self, tmp_path):
        """ratios with equal first/second half mean → trend 'stable'."""
        mod = load_status_module()
        records = [
            make_record(ratio=1.0, timestamp='2026-01-01T00:00:00Z'),
            make_record(ratio=1.2, timestamp='2026-01-08T00:00:00Z'),
            make_record(ratio=1.0, timestamp='2026-01-15T00:00:00Z'),
            make_record(ratio=1.2, timestamp='2026-01-22T00:00:00Z'),
        ]
        acc = mod.compute_accuracy(records, False)
        assert acc['trend'] == 'stable'

    def test_trend_degrading(self, tmp_path):
        """ratios [1.0, 1.5, 2.0, 2.5] → trend 'degrading'."""
        mod = load_status_module()
        records = [
            make_record(ratio=1.0, timestamp='2026-01-01T00:00:00Z'),
            make_record(ratio=1.5, timestamp='2026-01-08T00:00:00Z'),
            make_record(ratio=2.0, timestamp='2026-01-15T00:00:00Z'),
            make_record(ratio=2.5, timestamp='2026-01-22T00:00:00Z'),
        ]
        acc = mod.compute_accuracy(records, False)
        assert acc['trend'] == 'degrading'

    def test_pct_within_expected(self, tmp_path):
        """2 of 3 sessions in expected band → pct ~0.667.

        Third record uses stored pessimistic_cost so band_hit returns
        'over_pessimistic' without triggering is_outlier (ratio < 3.0).
        """
        mod = load_status_module()
        records = [
            # actual=5.0, opt=3.0, pess=15.0 → 3.0 < 5.0 <= 15.0 → 'expected'
            make_record(ratio=1.0, optimistic_cost=3.0, pessimistic_cost=15.0),
            # actual=6.0, opt=3.0, pess=15.0 → 'expected'
            make_record(ratio=1.2, optimistic_cost=3.0, pessimistic_cost=15.0),
            # ratio=1.8 (not outlier), actual=9.0 > pess=7.0 → 'over_pessimistic'
            make_record(ratio=1.8, optimistic_cost=3.0, pessimistic_cost=7.0),
        ]
        acc = mod.compute_accuracy(records, False)
        assert acc['pct_within_expected'] == pytest.approx(2 / 3, rel=0.01)

    def test_insufficient_data_trend(self, tmp_path):
        """1 record → trend 'insufficient_data'."""
        mod = load_status_module()
        records = [make_record(ratio=1.2)]
        acc = mod.compute_accuracy(records, False)
        assert acc['trend'] == 'insufficient_data'


# ---------------------------------------------------------------------------
# TestCostAttribution
# ---------------------------------------------------------------------------

class TestCostAttribution:
    def test_no_step_data(self, tmp_path):
        """Records without step_actuals → has_step_data False."""
        mod = load_status_module()
        records = [make_record(), make_record()]
        attr = mod.compute_cost_attribution(records)
        assert attr['has_step_data'] is False

    def test_step_totals_aggregated(self, tmp_path):
        """2 sessions each with {'Implementation': 1.0, 'QA': 0.5} → Implementation total=2.0."""
        mod = load_status_module()
        records = [
            make_record(step_actuals={'Implementation': 1.0, 'QA': 0.5}),
            make_record(step_actuals={'Implementation': 1.0, 'QA': 0.5}),
        ]
        attr = mod.compute_cost_attribution(records)
        totals = {s['step']: s['total'] for s in attr['steps']}
        assert totals['Implementation'] == pytest.approx(2.0)
        assert totals['QA'] == pytest.approx(1.0)

    def test_sorted_by_cost_descending(self, tmp_path):
        """Step with higher total appears first."""
        mod = load_status_module()
        records = [
            make_record(step_actuals={'QA': 0.5, 'Implementation': 3.0}),
        ]
        attr = mod.compute_cost_attribution(records)
        steps = attr['steps']
        assert len(steps) >= 2
        assert steps[0]['total'] >= steps[1]['total']

    def test_mixed_pre_v17(self, tmp_path):
        """Some records lack step_actuals → sessions_without_step_data > 0."""
        mod = load_status_module()
        records = [
            make_record(step_actuals={'Implementation': 2.0}),
            make_record(),  # no step_actuals
        ]
        attr = mod.compute_cost_attribution(records)
        assert attr['sessions_without_step_data'] > 0


# ---------------------------------------------------------------------------
# TestOutlierReport
# ---------------------------------------------------------------------------

class TestOutlierReport:
    def test_no_outliers(self, tmp_path):
        """All clean records → count == 0."""
        mod = load_status_module()
        records = [
            make_record(ratio=1.0),
            make_record(ratio=1.2),
            make_record(ratio=0.8),
        ]
        outliers = mod.compute_outliers(records, records)
        assert outliers['count'] == 0

    def test_high_ratio_outlier(self, tmp_path):
        """Record with ratio=4.0 → included in outliers."""
        mod = load_status_module()
        records = [
            make_record(ratio=4.0),
            make_record(ratio=1.0),
        ]
        outliers = mod.compute_outliers(records, records)
        assert outliers['count'] >= 1
        ratios = [o['ratio'] for o in outliers['records']]
        assert 4.0 in ratios

    def test_low_ratio_outlier(self, tmp_path):
        """Record with ratio=0.1 → included."""
        mod = load_status_module()
        records = [
            make_record(ratio=0.1),
            make_record(ratio=1.0),
        ]
        outliers = mod.compute_outliers(records, records)
        assert outliers['count'] >= 1
        ratios = [o['ratio'] for o in outliers['records']]
        assert 0.1 in ratios

    def test_excluded_not_in_outliers(self, tmp_path):
        """excluded=True record → not in outlier list."""
        mod = load_status_module()
        records = [
            make_record(ratio=5.0, excluded=True),
            make_record(ratio=1.0),
        ]
        outliers = mod.compute_outliers(records, records)
        assert outliers['count'] == 0


# ---------------------------------------------------------------------------
# TestRecommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    def test_review_cycles_high_fires(self, tmp_path):
        """3 records with review_cycles_actual=5, default=2 → rec emitted."""
        mod = load_status_module()
        records = [
            make_record(review_cycles_actual=5),
            make_record(review_cycles_actual=5),
            make_record(review_cycles_actual=5),
        ]
        recs = mod.compute_recommendations(records, records, {}, HEURISTICS_PATH, 2)
        types = [r['type'] for r in recs]
        assert any('review' in t.lower() or 'cycle' in t.lower() for t in types)

    def test_review_cycles_high_insufficient_data(self, tmp_path):
        """2 records → review cycle recommendation not emitted."""
        mod = load_status_module()
        records = [
            make_record(review_cycles_actual=5),
            make_record(review_cycles_actual=5),
        ]
        recs = mod.compute_recommendations(records, records, {}, HEURISTICS_PATH, 2)
        # Should not fire with fewer than 3 records
        cycle_recs = [r for r in recs if 'cycle' in r.get('type', '').lower() or
                      'review' in r.get('type', '').lower()]
        assert len(cycle_recs) == 0

    def test_bands_too_wide_fires(self, tmp_path):
        """5 records all within optimistic band → rec emitted."""
        mod = load_status_module()
        records = [
            make_record(ratio=0.4, optimistic_cost=3.0, pessimistic_cost=15.0)
            for _ in range(5)
        ]
        # actual = 5.0 * 0.4 = 2.0 <= optimistic_cost 3.0
        recs = mod.compute_recommendations(records, records, {}, HEURISTICS_PATH, 2)
        types = [r['type'] for r in recs]
        assert any('band' in t.lower() or 'wide' in t.lower() or 'optimistic' in t.lower()
                   for t in types)

    def test_bands_too_wide_insufficient_data(self, tmp_path):
        """4 records → bands too wide rec not emitted."""
        mod = load_status_module()
        records = [
            make_record(ratio=0.4, optimistic_cost=3.0, pessimistic_cost=15.0)
            for _ in range(4)
        ]
        recs = mod.compute_recommendations(records, records, {}, HEURISTICS_PATH, 2)
        band_recs = [r for r in recs if 'band' in r.get('type', '').lower() or
                     'wide' in r.get('type', '').lower()]
        assert len(band_recs) == 0

    def test_high_outlier_rate_fires(self, tmp_path):
        """6 records, 4 outliers → rec emitted (destructive=True)."""
        mod = load_status_module()
        records = [make_record(ratio=1.0)] * 2 + [make_record(ratio=4.0)] * 4
        outlier_records = [r for r in records if r['ratio'] > OUTLIER_HIGH]
        recs = mod.compute_recommendations(records, records, {}, HEURISTICS_PATH, 2)
        types = [r['type'] for r in recs]
        assert any('outlier' in t.lower() for t in types)
        # Should be marked destructive (high outlier rate is a data quality concern)
        outlier_recs = [r for r in recs if 'outlier' in r.get('type', '').lower()]
        assert any(r.get('destructive') is True for r in outlier_recs)

    def test_step_dominance_fires(self, tmp_path):
        """3+ sidecar records, one step >60% → rec emitted."""
        mod = load_status_module()
        records = [
            make_record(step_actuals={'Implementation': 9.0, 'QA': 1.0}),
            make_record(step_actuals={'Implementation': 9.0, 'QA': 1.0}),
            make_record(step_actuals={'Implementation': 9.0, 'QA': 1.0}),
        ]
        recs = mod.compute_recommendations(records, records, {}, HEURISTICS_PATH, 2)
        types = [r['type'] for r in recs]
        assert any('dominan' in t.lower() or 'step' in t.lower() for t in types)

    def test_rec_session_outlier_per_record(self, tmp_path):
        """rec_session_outlier(record) takes single record, returns None/dict (F11)."""
        mod = load_status_module()
        clean = make_record(ratio=1.0)
        high = make_record(ratio=4.0)
        result_clean = mod.rec_session_outlier(clean)
        result_high = mod.rec_session_outlier(high)
        # Clean record should return None
        assert result_clean is None
        # High outlier record should return a dict recommendation
        assert result_high is not None
        assert isinstance(result_high, dict)

    def test_no_false_positives_sparse(self, tmp_path):
        """2 records → no non-outlier-session recommendations."""
        mod = load_status_module()
        records = [make_record(ratio=1.1), make_record(ratio=0.9)]
        recs = mod.compute_recommendations(records, records, {}, HEURISTICS_PATH, 2)
        # With only 2 records, no threshold-based recs should fire
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# TestWindowResolution
# ---------------------------------------------------------------------------

class TestWindowResolution:
    def test_adaptive_small_history(self, tmp_path):
        """8 records → all 8 returned (below window threshold)."""
        mod = load_status_module()
        records = [
            make_record(timestamp=f'2026-01-{i+1:02d}T00:00:00Z')
            for i in range(8)
        ]
        result = mod.resolve_window(records, None)
        assert len(result) == 8

    def test_adaptive_large_history(self, tmp_path):
        """15 records spanning 40 days → rolling window applied (not all 15)."""
        mod = load_status_module()
        # Records spanning > 30 days; at least some should be outside the default window
        records = [
            make_record(timestamp=f'2026-01-{i+1:02d}T00:00:00Z')
            for i in range(15)
        ]
        result = mod.resolve_window(records, None)
        # Window should trim to DEFAULT_WINDOW_SESSIONS or DEFAULT_WINDOW_DAYS, not all 15
        assert len(result) <= DEFAULT_WINDOW_SESSIONS

    def test_window_count_spec(self, tmp_path):
        """window='5' → last 5 records."""
        mod = load_status_module()
        records = [make_record() for _ in range(10)]
        result = mod.resolve_window(records, '5')
        assert len(result) == 5

    def test_window_all_spec(self, tmp_path):
        """window='all' → all records."""
        mod = load_status_module()
        records = [make_record() for _ in range(20)]
        result = mod.resolve_window(records, 'all')
        assert len(result) == 20

    def test_empty_window(self, tmp_path):
        """window='5' with 0 recent records → empty list (no crash)."""
        mod = load_status_module()
        result = mod.resolve_window([], '5')
        assert result == []


# ---------------------------------------------------------------------------
# TestSparseBehavior
# ---------------------------------------------------------------------------

class TestSparseBehavior:
    def test_no_history_file(self, tmp_path):
        """Absent history → health.status in ('no_data', 'collecting')."""
        mod = load_status_module()
        absent_path = str(tmp_path / 'nonexistent.jsonl')
        records = mod.load_history(absent_path)
        factors_path = make_factors_file(tmp_path)
        health = mod.compute_health(records, mod.load_factors(factors_path))
        assert health['status'] in ('no_data', 'collecting')

    def test_one_record_not_verbose(self, tmp_path):
        """1 record → not_enough_data message, accuracy=None."""
        mod = load_status_module()
        records = [make_record(ratio=1.2)]
        factors_path = make_factors_file(tmp_path)
        result = mod.build_status_output(records, mod.load_factors(factors_path),
                                         verbose=False, window_spec=None)
        # With 1 record, accuracy should indicate insufficient data or be None
        assert result.get('accuracy') is None or \
               result['accuracy'].get('trend') == 'insufficient_data'

    def test_all_outliers(self, tmp_path):
        """All records are outliers → health status indicates problem."""
        mod = load_status_module()
        records = [make_record(ratio=5.0) for _ in range(5)]
        factors_path = make_factors_file(tmp_path)
        health = mod.compute_health(records, mod.load_factors(factors_path))
        # With all records being outliers, clean_count=0 → 'no_data'.
        # Any of these statuses is acceptable — they all indicate insufficient usable data.
        assert health['status'] in ('no_data', 'all_outliers', 'collecting')

    def test_verbose_bypasses_gate(self, tmp_path):
        """1 record + --verbose → schema_version in output."""
        history_path = make_history_file(tmp_path, [make_record(ratio=1.2)])
        factors_path = make_factors_file(tmp_path)
        rc, stdout, stderr = run_status_script(
            '--history', history_path,
            '--factors', factors_path,
            '--verbose', '--json'
        )
        assert rc == 0
        output = json.loads(stdout)
        assert 'schema_version' in output


# ---------------------------------------------------------------------------
# TestJsonOutput
# ---------------------------------------------------------------------------

class TestJsonOutput:
    def test_json_schema_version(self, tmp_path):
        """Output has schema_version=1 (E3)."""
        mod = load_status_module()
        records = [make_record() for _ in range(5)]
        factors_path = make_factors_file(tmp_path)
        result = mod.build_status_output(records, mod.load_factors(factors_path),
                                         verbose=True, window_spec=None)
        assert result.get('schema_version') == 1

    def test_json_required_top_level_keys(self, tmp_path):
        """health, accuracy, cost_attribution, outliers, recommendations all present."""
        mod = load_status_module()
        records = [make_record() for _ in range(5)]
        factors_path = make_factors_file(tmp_path)
        result = mod.build_status_output(records, mod.load_factors(factors_path),
                                         verbose=True, window_spec=None)
        for key in ('health', 'accuracy', 'cost_attribution', 'outliers', 'recommendations'):
            assert key in result, f'Missing required key: {key}'

    def test_json_parseable(self, tmp_path):
        """json.dumps(result) is valid (no TypeError)."""
        mod = load_status_module()
        records = [make_record() for _ in range(3)]
        factors_path = make_factors_file(tmp_path)
        result = mod.build_status_output(records, mod.load_factors(factors_path),
                                         verbose=True, window_spec=None)
        # Should not raise TypeError
        serialized = json.dumps(result)
        reparsed = json.loads(serialized)
        assert isinstance(reparsed, dict)


# ---------------------------------------------------------------------------
# TestStatusScriptIntegration (subprocess)
# ---------------------------------------------------------------------------

class TestStatusScriptIntegration:
    def test_script_invocation_with_history(self, tmp_path):
        """Run script with mock history (5 clean records) → exit 0, valid JSON."""
        history_path = make_history_file(tmp_path, [make_record() for _ in range(5)])
        factors_path = make_factors_file(tmp_path)
        rc, stdout, stderr = run_status_script(
            '--history', history_path,
            '--factors', factors_path,
            '--json'
        )
        assert rc == 0, f'Script exited {rc}: {stderr}'
        output = json.loads(stdout)
        assert isinstance(output, dict)

    def test_not_enough_data_message(self, tmp_path):
        """1-record history → health.message contains 'enough data'."""
        history_path = make_history_file(tmp_path, [make_record(ratio=1.2)])
        factors_path = make_factors_file(tmp_path)
        rc, stdout, stderr = run_status_script(
            '--history', history_path,
            '--factors', factors_path,
            '--json'
        )
        assert rc == 0
        output = json.loads(stdout)
        msg = output.get('health', {}).get('message', '')
        assert 'enough data' in msg.lower() or 'not enough' in msg.lower() or \
               'collecting' in msg.lower() or 'no_data' in output.get('health', {}).get('status', '')

    def test_verbose_flag(self, tmp_path):
        """1-record + --verbose → JSON with sections."""
        history_path = make_history_file(tmp_path, [make_record(ratio=1.2)])
        factors_path = make_factors_file(tmp_path)
        rc, stdout, stderr = run_status_script(
            '--history', history_path,
            '--factors', factors_path,
            '--verbose', '--json'
        )
        assert rc == 0
        output = json.loads(stdout)
        assert 'health' in output

    def test_json_flag(self, tmp_path):
        """--json flag → parseable JSON, exit 0."""
        history_path = make_history_file(tmp_path, [make_record() for _ in range(3)])
        factors_path = make_factors_file(tmp_path)
        rc, stdout, stderr = run_status_script(
            '--history', history_path,
            '--factors', factors_path,
            '--json'
        )
        assert rc == 0
        output = json.loads(stdout)
        assert isinstance(output, dict)
