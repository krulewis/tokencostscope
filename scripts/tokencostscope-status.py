"""tokencostscope-status.py — Calibration health dashboard for tokencostscope.

Reads calibration history and factors to produce a structured JSON report on
estimation accuracy, cost attribution, outliers, and actionable recommendations.

Usage:
    /usr/bin/python3 scripts/tokencostscope-status.py [options]

Options:
    --history PATH      Path to history.jsonl (default: calibration/history.jsonl)
    --factors PATH      Path to factors.json (default: calibration/factors.json)
    --heuristics PATH   Path to heuristics.md (default: references/heuristics.md)
    --window SPEC       Window spec: "30d", "10", "all", or omit for adaptive
    --verbose           Include per-session detail even when data is sparse
    --json              Output JSON (implies --no-apply)
    --no-apply          Do not apply recommendations automatically
"""

import argparse
import json
import math
import importlib.util
import pathlib
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# E2: Load calibration_store via importlib (both scripts/ files pattern)
# ---------------------------------------------------------------------------
_cs_path = pathlib.Path(__file__).parent / 'calibration_store.py'
_spec = importlib.util.spec_from_file_location('calibration_store', _cs_path)
calibration_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calibration_store)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
OUTLIER_HIGH = 3.0
OUTLIER_LOW = 0.2
DEFAULT_WINDOW_SESSIONS = 10
DEFAULT_WINDOW_DAYS = 30
STALE_PRICING_DAYS = 90
REVIEW_CYCLES_HIGH_THRESHOLD = 0.5
REVIEW_CYCLES_HIGH_MIN_SESSIONS = 3
BANDS_TOO_WIDE_PCT = 0.80
BANDS_TOO_NARROW_PCT = 0.30
BANDS_MIN_SESSIONS = 5
HIGH_OUTLIER_RATE_PCT = 0.50
HIGH_OUTLIER_RATE_MIN_RECORDS = 6
STEP_DOMINANCE_PCT = 0.60
STEP_DOMINANCE_MIN_SESSIONS = 3


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='tokencostscope calibration health dashboard'
    )
    parser.add_argument(
        '--history',
        default='calibration/history.jsonl',
        help='Path to history.jsonl (default: calibration/history.jsonl)'
    )
    parser.add_argument(
        '--factors',
        default='calibration/factors.json',
        help='Path to factors.json (default: calibration/factors.json)'
    )
    parser.add_argument(
        '--heuristics',
        default='references/heuristics.md',
        help='Path to heuristics.md (default: references/heuristics.md)'
    )
    parser.add_argument(
        '--window',
        default=None,
        help='Window spec: "30d" (days), "10" (sessions), "all", or omit for adaptive'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Include per-session detail even when data is sparse'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output JSON (implies --no-apply)'
    )
    parser.add_argument(
        '--no-apply',
        action='store_true',
        help='Do not apply recommendations automatically'
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Storage (E2: delegate to calibration_store)
# ---------------------------------------------------------------------------

def load_history(path: str) -> list:
    # E2: delegate to calibration_store.read_history(path)
    return calibration_store.read_history(path)


def load_factors(path: str) -> dict:
    # E2: delegate to calibration_store.read_factors(path)
    return calibration_store.read_factors(path)


# ---------------------------------------------------------------------------
# Heuristics parsing
# ---------------------------------------------------------------------------

def parse_heuristics_pricing_date(path: str):
    """Read heuristics.md, find 'last_updated' line, return date string or None."""
    try:
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('last_updated:'):
                    parts = stripped.split(':', 1)
                    if len(parts) == 2:
                        return parts[1].strip()
    except OSError:
        pass
    return None


def parse_review_cycles_default(path: str) -> int:
    """Read heuristics.md, find 'review_cycles_default' line, parse value. Return 2 if not found."""
    try:
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if 'review_cycles_default' in stripped and '|' in stripped:
                    # Table row format: | review_cycles_default   | 2     | ...
                    parts = [p.strip() for p in stripped.split('|')]
                    # parts[0] = '', parts[1] = param name, parts[2] = value, parts[3] = notes
                    if len(parts) >= 3:
                        try:
                            return int(parts[2])
                        except ValueError:
                            pass
    except OSError:
        pass
    return 2


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------

def get_ratio(record: dict) -> float:
    # F14: use stored ratio field when present
    ratio = record.get('ratio')
    if ratio is not None:
        return float(ratio)
    return record.get('actual_cost', 0) / max(record.get('expected_cost', 0.001), 0.001)


def is_outlier(record: dict) -> bool:
    r = get_ratio(record)
    return r > OUTLIER_HIGH or r < OUTLIER_LOW


def band_hit(record: dict) -> str:
    # F10: use stored costs when available — both required for consistent classification
    actual = record.get('actual_cost', 0)
    opt_cost = record.get('optimistic_cost')
    pess_cost = record.get('pessimistic_cost')
    if opt_cost is not None and pess_cost is not None:
        if actual <= opt_cost:
            return 'optimistic'
        if actual <= pess_cost:
            return 'expected'
        return 'over_pessimistic'
    # Fallback: ratio-based
    r = get_ratio(record)
    if r <= 0.6:
        return 'optimistic'
    if r <= 3.0:
        return 'expected'
    return 'over_pessimistic'


# ---------------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------------

def resolve_window(records: list, window_spec) -> list:
    """Parse window_spec and return the appropriate slice of records.

    None → adaptive: if total <= DEFAULT_WINDOW_SESSIONS → all; else rolling
      last DEFAULT_WINDOW_DAYS days or last DEFAULT_WINDOW_SESSIONS records,
      whichever is larger.
    "Nd" → last N days
    "N" (numeric) → last N records
    "all" → all records
    Does NOT filter excluded records.
    """
    if window_spec == 'all':
        return list(records)

    if window_spec is not None:
        spec = str(window_spec).strip()
        if spec.endswith('d'):
            # Days window
            try:
                n_days = int(spec[:-1])
            except ValueError:
                return list(records)
            cutoff_ts = _days_ago_timestamp(n_days)
            return [r for r in records if _record_timestamp(r) >= cutoff_ts]
        else:
            # Numeric session count
            try:
                n = int(spec)
                return records[-n:] if n < len(records) else list(records)
            except ValueError:
                return list(records)

    # Adaptive
    if len(records) <= DEFAULT_WINDOW_SESSIONS:
        return list(records)

    # Rolling last DEFAULT_WINDOW_DAYS days
    cutoff_ts = _days_ago_timestamp(DEFAULT_WINDOW_DAYS)
    by_days = [r for r in records if _record_timestamp(r) >= cutoff_ts]

    # Last DEFAULT_WINDOW_SESSIONS records
    by_count = records[-DEFAULT_WINDOW_SESSIONS:]

    # Return whichever is larger
    return by_days if len(by_days) >= len(by_count) else list(by_count)


def _days_ago_timestamp(n_days: int) -> float:
    """Return a Unix timestamp for n_days ago from now."""
    now = datetime.now(timezone.utc).timestamp()
    return now - n_days * 86400


def _record_timestamp(record: dict) -> float:
    """Return a comparable timestamp from a record. Returns 0 if unparseable."""
    ts = record.get('timestamp', '')
    if not ts:
        return 0.0
    try:
        # Try ISO format (with or without timezone)
        if ts.endswith('Z'):
            ts = ts[:-1] + '+00:00'
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Not-enough-data guard
# ---------------------------------------------------------------------------

def _not_enough_data(all_records: list, verbose: bool):
    """Returns not-enough-data response dict if conditions met (and not verbose), else None."""
    if verbose:
        return None

    if len(all_records) == 0:
        return _ned_response('no_data', 'No calibration data yet. Run a few sessions to start collecting data.', 0)

    clean = [r for r in all_records if not r.get('excluded') and not is_outlier(r)]
    if len(clean) < 3:
        return _ned_response(
            'collecting',
            f'Not enough data yet. {len(clean)} clean session(s) recorded; need at least 3 before analysis activates.',
            len(clean)
        )

    # All non-excluded records are outliers
    non_excluded = [r for r in all_records if not r.get('excluded')]
    if non_excluded and all(is_outlier(r) for r in non_excluded):
        return _ned_response(
            'all_outliers',
            'All non-excluded sessions are outliers. Consider excluding them or running more representative sessions.',
            0
        )

    return None


def _ned_response(status: str, message: str, clean_count: int) -> dict:
    return {
        'schema_version': 1,
        'health': {
            'status': status,
            'message': message,
            'clean_sample_count': clean_count,
            'active_factor_level': 'none',
            'factor_value': None,
        },
        'accuracy': None,
        'cost_attribution': None,
        'outliers': None,
        'recommendations': [],
        'window': None,
        'meta': None,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def compute_health(all_records: list, factors: dict) -> dict:
    clean = [r for r in all_records if not r.get('excluded') and not is_outlier(r)]
    clean_count = len(clean)

    if clean_count == 0:
        status = 'no_data'
    elif clean_count < 3:
        status = 'collecting'
    else:
        status = 'active'

    # Determine active_factor_level and factor_value
    active_factor_level = 'none'
    factor_value = None

    # Check per-step factors (any active)
    step_factors = factors.get('step_factors', {})
    if step_factors:
        for sf in step_factors.values():
            if isinstance(sf, dict) and sf.get('status') == 'active':
                active_factor_level = 'per-step'
                factor_value = sf.get('factor')
                break

    if active_factor_level == 'none':
        # Check per-signature factors (any active)
        sig_factors = factors.get('signature_factors', {})
        if sig_factors:
            for sf in sig_factors.values():
                if isinstance(sf, dict) and sf.get('status') == 'active':
                    active_factor_level = 'per-signature'
                    factor_value = sf.get('factor')
                    break

    if active_factor_level == 'none':
        # Check size-class factors (any factor with _n >= 3)
        size_factors = factors.get('size_factors', {})
        if not size_factors:
            # Try alternate key name
            size_factors = factors.get('size_class_factors', {})
        for sf in size_factors.values():
            if isinstance(sf, dict) and sf.get('n', 0) >= 3:
                active_factor_level = 'size-class'
                factor_value = sf.get('factor')
                break

    if active_factor_level == 'none':
        # Check global factor
        global_factor = factors.get('global', {})
        if isinstance(global_factor, dict) and global_factor.get('status') == 'active':
            active_factor_level = 'global'
            factor_value = global_factor.get('factor')

    # Status message
    if status == 'no_data':
        message = 'No calibration data yet.'
    elif status == 'collecting':
        message = f'Collecting data. {clean_count}/3 clean sessions needed for activation.'
    else:
        message = f'Active. {clean_count} clean sessions. Factor level: {active_factor_level}.'

    return {
        'status': status,
        'clean_sample_count': clean_count,
        'active_factor_level': active_factor_level,
        'factor_value': factor_value,
        'message': message,
    }


# ---------------------------------------------------------------------------
# Accuracy
# ---------------------------------------------------------------------------

def compute_accuracy(windowed_records: list, verbose: bool) -> dict:
    clean = [r for r in windowed_records if not r.get('excluded') and not is_outlier(r)]
    ratios = [get_ratio(r) for r in clean]

    # Trend computation
    if len(ratios) < 2:
        trend = 'insufficient_data'
    else:
        mid = len(ratios) // 2
        first_half = ratios[:mid]
        second_half = ratios[mid:]
        first_mean = sum(first_half) / len(first_half)
        second_mean = sum(second_half) / len(second_half)
        delta = second_mean - first_mean
        if delta < -0.05:
            trend = 'improving'
        elif delta > 0.05:
            trend = 'degrading'
        else:
            trend = 'stable'

    # Band hit percentages
    if clean:
        pct_within_expected = sum(
            1 for r in clean if band_hit(r) in ('optimistic', 'expected')
        ) / len(clean)
        pct_within_pessimistic = sum(
            1 for r in clean if band_hit(r) != 'over_pessimistic'
        ) / len(clean)
        mean_ratio = sum(ratios) / len(ratios)
        sorted_ratios = sorted(ratios)
        mid = len(sorted_ratios) // 2
        if len(sorted_ratios) % 2 == 0:
            median_ratio = (sorted_ratios[mid - 1] + sorted_ratios[mid]) / 2
        else:
            median_ratio = sorted_ratios[mid]
    else:
        pct_within_expected = 0.0
        pct_within_pessimistic = 0.0
        mean_ratio = None
        median_ratio = None

    # Per-session list
    sessions = []
    for r in clean:
        sessions.append({
            'timestamp': r.get('timestamp'),
            'size': r.get('size'),
            'ratio': get_ratio(r),
            'expected_cost': r.get('expected_cost'),
            'actual_cost': r.get('actual_cost'),
            'band': band_hit(r),
        })

    return {
        'mean_ratio': mean_ratio,
        'median_ratio': median_ratio,
        'trend': trend,
        'pct_within_expected': pct_within_expected,
        'pct_within_pessimistic': pct_within_pessimistic,
        'sessions': sessions,
    }


# ---------------------------------------------------------------------------
# Cost attribution
# ---------------------------------------------------------------------------

def compute_cost_attribution(windowed_records: list) -> dict:
    sidecar_records = [r for r in windowed_records if r.get('step_actuals')]

    # Aggregate per-step totals
    step_actual = {}
    step_estimated = {}
    for r in sidecar_records:
        for step_name, step_cost in r['step_actuals'].items():
            step_actual[step_name] = step_actual.get(step_name, 0.0) + step_cost
            step_estimated[step_name] = step_estimated.get(step_name, 0.0) + \
                r.get('step_costs_estimated', {}).get(step_name, 0.0)

    # Build sorted steps list
    steps = []
    for step_name in sorted(step_actual.keys(), key=lambda s: step_actual[s], reverse=True):
        est = step_estimated[step_name]
        acc_ratio = step_actual[step_name] / est if est > 0 else None
        steps.append({
            'step': step_name,
            'total': step_actual[step_name],
            'estimated_total': est,
            'accuracy_ratio': acc_ratio,
        })

    sessions_without = len(windowed_records) - len(sidecar_records)
    note = None
    if sessions_without > 0:
        note = f'{sessions_without} session(s) in window have no step-level data.'

    return {
        'has_step_data': len(sidecar_records) > 0,
        'steps': steps,
        'sessions_with_step_data': len(sidecar_records),
        'sessions_without_step_data': sessions_without,
        'note': note,
    }


# ---------------------------------------------------------------------------
# Outliers
# ---------------------------------------------------------------------------

def compute_outliers(all_records: list, windowed_records: list) -> dict:
    outliers = [r for r in all_records if not r.get('excluded') and is_outlier(r)]
    outlier_rate = len(outliers) / max(len(all_records), 1)

    # Pattern detection
    patterns = []

    # Check if 2+ outliers share same size
    size_counts = {}
    for r in outliers:
        s = r.get('size')
        if s:
            size_counts[s] = size_counts.get(s, 0) + 1
    for size, count in size_counts.items():
        if count >= 2:
            patterns.append(f'{count} outliers share size={size}')

    # Check if 2+ outliers share same project_type
    pt_counts = {}
    for r in outliers:
        pt = r.get('project_type')
        if pt:
            pt_counts[pt] = pt_counts.get(pt, 0) + 1
    for pt, count in pt_counts.items():
        if count >= 2:
            patterns.append(f'{count} outliers share project_type={pt}')

    # Build records list with probable_cause
    records_out = []
    for r in outliers:
        ratio = get_ratio(r)
        if ratio > OUTLIER_HIGH:
            probable_cause = 'overrun — actual cost exceeded expected by more than 3×'
        else:
            probable_cause = 'underrun — actual cost was less than 20% of expected'
        records_out.append({
            'timestamp': r.get('timestamp'),
            'size': r.get('size'),
            'ratio': ratio,
            'expected_cost': r.get('expected_cost'),
            'actual_cost': r.get('actual_cost'),
            'probable_cause': probable_cause,
        })

    return {
        'count': len(outliers),
        'outlier_rate': outlier_rate,
        'records': records_out,
        'patterns': patterns,
    }


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

def rec_session_outlier(record: dict):
    """F11: single record. Returns None or one dict."""
    if record.get('excluded') or not is_outlier(record):
        return None
    ratio = get_ratio(record)
    direction = 'overrun' if ratio > OUTLIER_HIGH else 'underrun'
    return {
        'type': 'session_outlier',
        'description': (
            f'Session at {record.get("timestamp", "unknown")} is an outlier '
            f'(ratio={ratio:.2f}, {direction}). Consider excluding it from calibration.'
        ),
        'supporting_data': {
            'timestamp': record.get('timestamp'),
            'ratio': ratio,
            'actual_cost': record.get('actual_cost'),
            'expected_cost': record.get('expected_cost'),
            'size': record.get('size'),
        },
        'action': {
            'type': 'exclude_session',
            'session_timestamp': record.get('timestamp'),
        },
        'destructive': True,
        'priority': 'informational',
    }


def rec_review_cycles_high(records: list, review_cycles_default: int):
    rc_records = [r for r in records if r.get('review_cycles_actual') is not None]
    if len(rc_records) < REVIEW_CYCLES_HIGH_MIN_SESSIONS:
        return None
    mean_actual = sum(r['review_cycles_actual'] for r in rc_records) / len(rc_records)
    if mean_actual <= review_cycles_default + REVIEW_CYCLES_HIGH_THRESHOLD:
        return None
    proposed = int(math.ceil(mean_actual))
    return {
        'type': 'review_cycles_high',
        'description': (
            f'Average review cycles ({mean_actual:.1f}) consistently exceeds the default '
            f'({review_cycles_default}). Increasing the default would improve estimate accuracy.'
        ),
        'supporting_data': {
            'mean_actual_cycles': mean_actual,
            'current_default': review_cycles_default,
            'proposed_value': proposed,
            'sessions_with_data': len(rc_records),
        },
        'action': {
            'type': 'edit_heuristic',
            'parameter': 'review_cycles_default',
            'current_value': str(review_cycles_default),
            'proposed_value': str(proposed),
            'file': 'references/heuristics.md',
        },
        'destructive': False,
        'priority': 'accuracy',
    }


def rec_bands_too_wide(records: list):
    clean = [r for r in records if not r.get('excluded') and not is_outlier(r)]
    if len(clean) < BANDS_MIN_SESSIONS:
        return None
    pct_opt = sum(1 for r in clean if band_hit(r) == 'optimistic') / len(clean)
    if pct_opt <= BANDS_TOO_WIDE_PCT:
        return None
    return {
        'type': 'bands_too_wide',
        'description': (
            f'{pct_opt:.0%} of sessions land in the optimistic band. '
            'Estimates may be too conservative — consider tightening the pessimistic multiplier.'
        ),
        'supporting_data': {
            'pct_optimistic': pct_opt,
            'threshold': BANDS_TOO_WIDE_PCT,
            'clean_sessions': len(clean),
        },
        'action': {'type': 'guidance'},
        'destructive': False,
        'priority': 'guidance',
    }


def rec_bands_too_narrow(records: list):
    clean = [r for r in records if not r.get('excluded') and not is_outlier(r)]
    if len(clean) < BANDS_MIN_SESSIONS:
        return None
    pct_over = sum(1 for r in clean if band_hit(r) == 'over_pessimistic') / len(clean)
    if pct_over <= BANDS_TOO_NARROW_PCT:
        return None
    return {
        'type': 'bands_too_narrow',
        'description': (
            f'{pct_over:.0%} of sessions exceed the pessimistic band. '
            'Estimates may be too optimistic — consider widening the pessimistic multiplier.'
        ),
        'supporting_data': {
            'pct_over_pessimistic': pct_over,
            'threshold': BANDS_TOO_NARROW_PCT,
            'clean_sessions': len(clean),
        },
        'action': {'type': 'guidance'},
        'destructive': False,
        'priority': 'guidance',
    }


def rec_high_outlier_rate(all_records: list):
    non_excluded = [r for r in all_records if not r.get('excluded')]
    if len(non_excluded) < HIGH_OUTLIER_RATE_MIN_RECORDS:
        return None
    outlier_count = sum(1 for r in non_excluded if is_outlier(r))
    if outlier_count / len(non_excluded) <= HIGH_OUTLIER_RATE_PCT:
        return None
    return {
        'type': 'high_outlier_rate',
        'description': (
            f'{outlier_count}/{len(non_excluded)} sessions are outliers '
            f'({outlier_count / len(non_excluded):.0%}). Calibration factors may be unreliable. '
            'Consider resetting calibration data.'
        ),
        'supporting_data': {
            'outlier_count': outlier_count,
            'total_non_excluded': len(non_excluded),
            'outlier_rate': outlier_count / len(non_excluded),
        },
        'action': {'type': 'reset_calibration'},
        'destructive': True,
        'priority': 'accuracy',
    }


def rec_step_dominance(records: list):
    sidecar = [r for r in records if r.get('step_actuals')]
    if len(sidecar) < STEP_DOMINANCE_MIN_SESSIONS:
        return None

    # Aggregate totals per step
    step_totals = {}
    for r in sidecar:
        for step_name, step_cost in r['step_actuals'].items():
            step_totals[step_name] = step_totals.get(step_name, 0.0) + step_cost

    if not step_totals:
        return None

    grand_total = sum(step_totals.values())
    if grand_total == 0:
        return None

    dominant_step = max(step_totals, key=lambda s: step_totals[s])
    dominant_pct = step_totals[dominant_step] / grand_total

    if dominant_pct <= STEP_DOMINANCE_PCT:
        return None

    return {
        'type': 'step_dominance',
        'description': (
            f'Step "{dominant_step}" accounts for {dominant_pct:.0%} of total cost '
            f'across {len(sidecar)} sessions. '
            'Review whether this step is appropriately scoped in estimates.'
        ),
        'supporting_data': {
            'dominant_step': dominant_step,
            'dominant_pct': dominant_pct,
            'dominant_total': step_totals[dominant_step],
            'grand_total': grand_total,
            'sessions_with_data': len(sidecar),
        },
        'action': {'type': 'guidance'},
        'destructive': False,
        'priority': 'guidance',
    }


def rec_stale_pricing(heuristics_path: str):
    date_str = parse_heuristics_pricing_date(heuristics_path)
    if not date_str:
        return None
    try:
        pricing_date = datetime.fromisoformat(date_str)
        if pricing_date.tzinfo is None:
            pricing_date = pricing_date.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days_old = (now - pricing_date).days
    except (ValueError, TypeError):
        return None
    if days_old <= STALE_PRICING_DAYS:
        return None
    return {
        'type': 'stale_pricing',
        'description': (
            f'Pricing data in heuristics.md was last updated {days_old} days ago '
            f'(>{STALE_PRICING_DAYS} days threshold). Verify current pricing at anthropic.com/pricing.'
        ),
        'supporting_data': {
            'last_updated': date_str,
            'days_old': days_old,
            'threshold_days': STALE_PRICING_DAYS,
        },
        'action': {'type': 'guidance'},
        'destructive': False,
        'priority': 'informational',
    }


_PRIORITY_ORDER = {'accuracy': 0, 'guidance': 1, 'informational': 2}


def compute_recommendations(
    windowed_records: list,
    all_records: list,
    factors: dict,
    heuristics_path: str,
    review_cycles_default: int,
) -> list:
    recs = []

    r = rec_review_cycles_high(windowed_records, review_cycles_default)
    if r is not None:
        recs.append(r)

    r = rec_bands_too_wide(windowed_records)
    if r is not None:
        recs.append(r)

    r = rec_bands_too_narrow(windowed_records)
    if r is not None:
        recs.append(r)

    r = rec_high_outlier_rate(all_records)
    if r is not None:
        recs.append(r)

    r = rec_step_dominance(windowed_records)
    if r is not None:
        recs.append(r)

    r = rec_stale_pricing(heuristics_path)
    if r is not None:
        recs.append(r)

    # F11: iterate per record for session outliers
    for record in windowed_records:
        r = rec_session_outlier(record)
        if r is not None:
            recs.append(r)

    # Sort: priority order "accuracy" > "guidance" > "informational"
    recs.sort(key=lambda x: _PRIORITY_ORDER.get(x.get('priority', 'informational'), 2))

    return recs


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyze(args) -> dict:
    all_records = load_history(args.history)   # E2
    factors = load_factors(args.factors)       # E2

    # Sparse/empty handling
    ned = _not_enough_data(all_records, args.verbose)
    if ned is not None:
        ned['window'] = {
            'spec': args.window,
            'records_in_window': 0,
            'total_records': len(all_records),
        }
        ned['meta'] = {
            'verbose': args.verbose,
            'no_apply': args.no_apply or args.json,
        }
        return ned

    windowed = resolve_window(all_records, args.window)
    rc_default = parse_review_cycles_default(args.heuristics)
    health = compute_health(all_records, factors)
    accuracy = compute_accuracy(windowed, args.verbose)
    cost_attribution = compute_cost_attribution(windowed)
    outliers = compute_outliers(all_records, windowed)
    recs = compute_recommendations(windowed, all_records, factors, args.heuristics, rc_default)

    # JSON OUTPUT SCHEMA CONTRACT (E3): schema_version=1 is a versioned API contract.
    # Consumers of this output should check schema_version before parsing fields.
    # Breaking changes (field removals, type changes) require a schema_version bump.
    # Additive changes (new optional fields) do not require a version bump.
    return {
        'schema_version': 1,
        'health': health,
        'accuracy': accuracy,
        'cost_attribution': cost_attribution,
        'outliers': outliers,
        'recommendations': recs,
        'window': {
            'spec': args.window,
            'records_in_window': len(windowed),
            'total_records': len(all_records),
        },
        'meta': {
            'verbose': args.verbose,
            'no_apply': args.no_apply or args.json,
        },
    }


def build_status_output(all_records, factors, verbose=False, window_spec=None,
                        heuristics_path=None):
    """Testable helper: compute status output from pre-loaded data (no file I/O).

    Mirrors analyze() but accepts already-loaded records/factors instead of an args
    namespace. Used by tests and any caller that has already loaded the data.
    """
    if heuristics_path is None:
        heuristics_path = str(
            pathlib.Path(__file__).parent.parent / 'references' / 'heuristics.md'
        )
    ned = _not_enough_data(all_records, verbose)
    if ned is not None:
        ned['window'] = {
            'spec': window_spec,
            'records_in_window': 0,
            'total_records': len(all_records),
        }
        ned['meta'] = {'verbose': verbose, 'no_apply': False}
        return ned
    windowed = resolve_window(all_records, window_spec)
    rc_default = parse_review_cycles_default(heuristics_path)
    health = compute_health(all_records, factors)
    accuracy = compute_accuracy(windowed, verbose)
    cost_attribution = compute_cost_attribution(windowed)
    outliers = compute_outliers(all_records, windowed)
    recs = compute_recommendations(windowed, all_records, factors, heuristics_path, rc_default)
    return {
        'schema_version': 1,
        'health': health,
        'accuracy': accuracy,
        'cost_attribution': cost_attribution,
        'outliers': outliers,
        'recommendations': recs,
        'window': {
            'spec': window_spec,
            'records_in_window': len(windowed),
            'total_records': len(all_records),
        },
        'meta': {'verbose': verbose, 'no_apply': False},
    }


def main():
    args = parse_args()
    result = analyze(args)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
