"""Shared helpers for calibration convergence tests.

Provides synthetic-data construction and calibration loop execution.
"""

import importlib.util
import json
import math
import os
import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
UPDATE_FACTORS_PY = REPO_ROOT / "scripts" / "update-factors.py"

_spec = importlib.util.spec_from_file_location("update_factors", str(UPDATE_FACTORS_PY))
assert _spec is not None, f"Could not load spec for {UPDATE_FACTORS_PY}"
assert _spec.loader is not None, "Spec loader is None"
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
update_factors = _mod.update_factors


def make_session_record(
    days_ago,
    ratio=1.0,
    size="M",
    steps=None,
    step_ratios=None,
    pipeline_signature=None,
    expected_cost=5.0,
    **kwargs,
):
    """Create a minimal history record with timestamp set to days_ago days in the past.

    Parameters
    ----------
    days_ago : float
        How many days ago this session occurred. Must be > 0.
    ratio : float
        actual_cost = expected_cost * ratio.
    size : str
        Pipeline size classification ("S", "M", "L", "XS").
    steps : list[str] or None
        If provided, added as "steps" key; canonical signature is derived by
        update_factors from this array. Do NOT set pipeline_signature when
        using steps — the derivation takes precedence.
    step_ratios : dict or None
        Per-step ratio dict (used by US-CV-08).
    pipeline_signature : str or None
        Used when caller wants to set a signature without specifying step names.
        Ignored if steps is also provided.
    expected_cost : float
        Base expected cost; actual_cost = expected_cost * ratio.
    **kwargs
        Additional fields merged into the record (e.g., excluded=True).
    """
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    record = {
        "timestamp": ts,
        "size": size,
        "expected_cost": expected_cost,
        "actual_cost": expected_cost * ratio,
    }
    if steps is not None:
        record["steps"] = steps
    elif pipeline_signature is not None:
        record["pipeline_signature"] = pipeline_signature
    if step_ratios is not None:
        record["step_ratios"] = step_ratios
    record.update(kwargs)
    return record


def run_calibration_loop(records):
    """Write records to a temp history.jsonl, call update_factors, return factors dict.

    Parameters
    ----------
    records : list[dict]
        Session history records as produced by make_session_record().

    Returns
    -------
    dict
        Parsed factors.json, or {} if update_factors did not produce output.
    """
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


def incremental_calibration(sessions):
    """Simulate sequential session processing, returning factors after each session.

    Mirrors production behavior: update_factors reads the full history.jsonl from
    scratch each time. A single TemporaryDirectory is kept open for all iterations.

    Parameters
    ----------
    sessions : list[dict]
        Ordered list of session records. sessions[0] is the earliest session.

    Returns
    -------
    list[dict]
        factors_list[i] is the factors.json state after session i+1 has been processed.
        Length equals len(sessions).
    """
    factors_list = []
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = os.path.join(tmpdir, "history.jsonl")
        factors_path = os.path.join(tmpdir, "factors.json")
        # Ensure empty history file exists at start
        open(history_path, "w").close()
        for session in sessions:
            with open(history_path, "a") as f:
                f.write(json.dumps(session) + "\n")
            update_factors(history_path, factors_path)
            if os.path.exists(factors_path):
                with open(factors_path) as f:
                    factors_list.append(json.load(f))
            else:
                factors_list.append({})
    return factors_list


def compute_relative_error(actual, target):
    """Return |actual - target| / target.

    Returns float('inf') if target == 0.0.
    """
    if target == 0.0:
        return float("inf")
    return abs(actual - target) / target
