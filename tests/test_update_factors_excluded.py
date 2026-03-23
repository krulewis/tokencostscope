"""Tests for excluded field handling in update-factors.py (v1.7.0 Change 3/Finding F9).

Tests that records with excluded=true (boolean or truthy) are skipped during
Pass 1 of update_factors(), while records with excluded=False or missing excluded
are included normally.

All tests call update_factors() directly. They should FAIL before Change 3 is
implemented (excluded field not yet handled), and PASS after implementation.
"""
# Runner: pytest (required). Use: /usr/bin/python3 -m pytest tests/

import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
UPDATE_FACTORS_PY = SCRIPTS_DIR / "update-factors.py"


def load_update_factors():
    """Dynamically load update-factors.py (has hyphens in name)."""
    spec = importlib.util.spec_from_file_location(
        "update_factors", str(UPDATE_FACTORS_PY)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_record(ratio=1.0, excluded=None, size="M", ts="2026-01-01T00:00:00Z"):
    """Build a minimal valid history record. If excluded is None, field is omitted."""
    rec = {
        "timestamp": ts,
        "size": size,
        "expected_cost": 5.0,
        "actual_cost": 5.0 * ratio,
    }
    if excluded is not None:
        rec["excluded"] = excluded
    return rec


def write_history(tmp_path, records):
    """Write records as JSONL, return path."""
    path = os.path.join(tmp_path, "history.jsonl")
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def read_factors(tmp_path):
    """Read factors.json if it exists, else return None."""
    path = os.path.join(tmp_path, "factors.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


class TestExcludedField:
    """Tests for excluded field handling in update-factors.py Pass 1."""

    def test_excluded_true_boolean_not_counted(self, tmp_path):
        """Record with excluded=True (boolean) is skipped; not counted in sample_count."""
        uf = load_update_factors()
        records = [make_record(ratio=1.0, excluded=True)]
        hist = write_history(str(tmp_path), records)
        factors_path = str(tmp_path / "factors.json")
        uf.update_factors(hist, factors_path)
        result = read_factors(str(tmp_path))
        assert result is not None
        assert result["sample_count"] == 0

    def test_excluded_false_boolean_counted(self, tmp_path):
        """Record with excluded=False is included in sample_count."""
        uf = load_update_factors()
        # Need 3+ records to produce active status; use 3 records with excluded=False
        records = [make_record(ratio=1.0, excluded=False) for _ in range(3)]
        hist = write_history(str(tmp_path), records)
        factors_path = str(tmp_path / "factors.json")
        uf.update_factors(hist, factors_path)
        result = read_factors(str(tmp_path))
        assert result is not None
        assert result["sample_count"] == 3

    def test_missing_excluded_field_included(self, tmp_path):
        """Record without excluded field is included (default behavior)."""
        uf = load_update_factors()
        records = [make_record(ratio=1.0) for _ in range(3)]
        # No excluded key on any record
        for r in records:
            assert "excluded" not in r
        hist = write_history(str(tmp_path), records)
        factors_path = str(tmp_path / "factors.json")
        uf.update_factors(hist, factors_path)
        result = read_factors(str(tmp_path))
        assert result is not None
        assert result["sample_count"] == 3

    def test_all_excluded_results_in_collecting(self, tmp_path):
        """All records excluded → sample_count=0 → collecting status (< 3 samples)."""
        uf = load_update_factors()
        records = [make_record(ratio=1.0, excluded=True) for _ in range(5)]
        hist = write_history(str(tmp_path), records)
        factors_path = str(tmp_path / "factors.json")
        uf.update_factors(hist, factors_path)
        result = read_factors(str(tmp_path))
        assert result is not None
        assert result["sample_count"] == 0
        assert result["status"] == "collecting"

    def test_excluded_does_not_affect_outlier_count(self, tmp_path):
        """Excluded records are removed before outlier detection — not counted as outliers."""
        uf = load_update_factors()
        # An excluded record with a wild ratio should not appear in outlier_count
        records = [
            make_record(ratio=1.0, excluded=True),   # excluded — ratio=1.0, not an outlier anyway
            make_record(ratio=50.0, excluded=True),  # excluded with extreme ratio — not counted
            make_record(ratio=1.0),                  # clean
        ]
        hist = write_history(str(tmp_path), records)
        factors_path = str(tmp_path / "factors.json")
        uf.update_factors(hist, factors_path)
        result = read_factors(str(tmp_path))
        assert result is not None
        # Only 1 clean record made it to Pass 2; outlier_count reflects only clean-set outliers
        assert result["outlier_count"] == 0
        assert result["sample_count"] == 1  # 1 clean non-outlier record; excluded records not counted

    def test_mix_excluded_and_clean_records(self, tmp_path):
        """3 clean + 1 excluded → sample_count=3, status active."""
        uf = load_update_factors()
        records = [
            make_record(ratio=1.0),
            make_record(ratio=0.9),
            make_record(ratio=1.1),
            make_record(ratio=2.0, excluded=True),  # excluded — should not affect count
        ]
        hist = write_history(str(tmp_path), records)
        factors_path = str(tmp_path / "factors.json")
        uf.update_factors(hist, factors_path)
        result = read_factors(str(tmp_path))
        assert result is not None
        assert result["sample_count"] == 3
        assert result["status"] == "active"

    def test_excluded_true_string_is_excluded(self, tmp_path):
        """F9: String "true" IS excluded (Python bool("true") = True, truthy).

        Users should use JSON boolean true, not the string "true".
        But if they do write the string, it should be treated as excluded.
        """
        uf = load_update_factors()
        # String "true" is truthy in Python — if rec.get("excluded", False) is evaluated
        # with the string "true", it evaluates truthy.
        records = [make_record(ratio=1.0, excluded="true")]
        hist = write_history(str(tmp_path), records)
        factors_path = str(tmp_path / "factors.json")
        uf.update_factors(hist, factors_path)
        result = read_factors(str(tmp_path))
        assert result is not None
        # "true" string is truthy → excluded → sample_count should be 0
        assert result["sample_count"] == 0

    def test_excluded_zero_not_excluded(self, tmp_path):
        """excluded=0 is falsy → record IS included (not excluded)."""
        uf = load_update_factors()
        records = [make_record(ratio=1.0, excluded=0) for _ in range(3)]
        hist = write_history(str(tmp_path), records)
        factors_path = str(tmp_path / "factors.json")
        uf.update_factors(hist, factors_path)
        result = read_factors(str(tmp_path))
        assert result is not None
        # excluded=0 is falsy → all 3 records included
        assert result["sample_count"] == 3
