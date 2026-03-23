"""Tests for calibration_store.py (v1.7.0 Change 14 / Enterprise Constraint E2).

Tests storage abstraction operations: read_history, append_history,
read_factors, write_factors, and the CLI append-history command.

All tests should FAIL before calibration_store.py is implemented,
and PASS once the module exists.
"""
# Runner: pytest (required). Use: /usr/bin/python3 -m pytest tests/

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CALIBRATION_STORE_PY = SCRIPTS_DIR / "calibration_store.py"
UPDATE_FACTORS_PY = SCRIPTS_DIR / "update-factors.py"


def load_calibration_store():
    """Dynamically load calibration_store.py."""
    if not CALIBRATION_STORE_PY.exists():
        pytest.skip(f"calibration_store.py not yet implemented: {CALIBRATION_STORE_PY}")
    spec = importlib.util.spec_from_file_location(
        "calibration_store", str(CALIBRATION_STORE_PY)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_record(ratio=1.0, size="M"):
    """Build a minimal valid history record."""
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "size": size,
        "expected_cost": 5.0,
        "actual_cost": 5.0 * ratio,
        "ratio": ratio,
    }


def make_history_file(tmp_path, records):
    """Write records as JSONL, return path string."""
    path = str(tmp_path / "history.jsonl")
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


class TestCalibrationStore:
    """Unit tests for calibration_store.py storage functions."""

    # -----------------------------------------------------------------------
    # read_history
    # -----------------------------------------------------------------------

    def test_read_history_absent_file(self, tmp_path):
        """Missing history file → empty list, no exception."""
        cs = load_calibration_store()
        missing = str(tmp_path / "nonexistent.jsonl")
        result = cs.read_history(missing)
        assert result == []

    def test_read_history_empty_file(self, tmp_path):
        """Empty history file → empty list."""
        cs = load_calibration_store()
        path = str(tmp_path / "history.jsonl")
        open(path, "w").close()
        result = cs.read_history(path)
        assert result == []

    def test_read_history_parses_records(self, tmp_path):
        """Three records → list of 3 dicts."""
        cs = load_calibration_store()
        records = [make_record(ratio=0.9), make_record(ratio=1.0), make_record(ratio=1.1)]
        path = make_history_file(tmp_path, records)
        result = cs.read_history(path)
        assert len(result) == 3
        assert result[0]["ratio"] == 0.9
        assert result[1]["ratio"] == 1.0
        assert result[2]["ratio"] == 1.1

    def test_read_history_skips_malformed_lines(self, tmp_path):
        """Bad JSON line skipped; other records parsed normally."""
        cs = load_calibration_store()
        path = str(tmp_path / "history.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(make_record(ratio=0.9)) + "\n")
            f.write("not valid json at all {{{{\n")
            f.write(json.dumps(make_record(ratio=1.1)) + "\n")
        result = cs.read_history(path)
        assert len(result) == 2
        assert result[0]["ratio"] == 0.9
        assert result[1]["ratio"] == 1.1

    def test_read_history_skips_blank_lines(self, tmp_path):
        """Blank lines produce no records."""
        cs = load_calibration_store()
        path = str(tmp_path / "history.jsonl")
        with open(path, "w") as f:
            f.write("\n")
            f.write(json.dumps(make_record(ratio=1.0)) + "\n")
            f.write("\n\n")
        result = cs.read_history(path)
        assert len(result) == 1

    # -----------------------------------------------------------------------
    # append_history
    # -----------------------------------------------------------------------

    def test_append_creates_file(self, tmp_path):
        """append_history on absent file creates file and parent dirs."""
        cs = load_calibration_store()
        nested = str(tmp_path / "subdir" / "history.jsonl")
        record = make_record(ratio=1.0)
        cs.append_history(nested, record)
        assert os.path.exists(nested)

    def test_append_adds_record(self, tmp_path):
        """append_history on existing file adds new record; old record preserved."""
        cs = load_calibration_store()
        existing_record = make_record(ratio=0.8)
        path = make_history_file(tmp_path, [existing_record])
        new_record = make_record(ratio=1.2)
        cs.append_history(path, new_record)
        result = cs.read_history(path)
        assert len(result) == 2
        assert result[0]["ratio"] == 0.8
        assert result[1]["ratio"] == 1.2

    def test_append_writes_valid_json(self, tmp_path):
        """Appended line is parseable JSON."""
        cs = load_calibration_store()
        path = str(tmp_path / "history.jsonl")
        record = make_record(ratio=1.0)
        cs.append_history(path, record)
        with open(path) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["ratio"] == 1.0

    # -----------------------------------------------------------------------
    # read_factors
    # -----------------------------------------------------------------------

    def test_read_factors_absent_file(self, tmp_path):
        """Missing factors.json → empty dict, no exception."""
        cs = load_calibration_store()
        missing = str(tmp_path / "factors.json")
        result = cs.read_factors(missing)
        assert result == {}

    def test_read_factors_malformed_json(self, tmp_path):
        """Malformed factors.json → empty dict, no exception."""
        cs = load_calibration_store()
        path = str(tmp_path / "factors.json")
        with open(path, "w") as f:
            f.write("this is not json {{{")
        result = cs.read_factors(path)
        assert result == {}

    def test_read_factors_valid(self, tmp_path):
        """Valid factors.json → correct dict returned."""
        cs = load_calibration_store()
        factors = {"global": 1.05, "sample_count": 5, "status": "active"}
        path = str(tmp_path / "factors.json")
        with open(path, "w") as f:
            json.dump(factors, f)
        result = cs.read_factors(path)
        assert result["global"] == 1.05
        assert result["status"] == "active"
        assert result["sample_count"] == 5

    # -----------------------------------------------------------------------
    # write_factors
    # -----------------------------------------------------------------------

    def test_write_factors_atomic(self, tmp_path):
        """write_factors uses temp file + rename; result is readable JSON."""
        cs = load_calibration_store()
        path = str(tmp_path / "factors.json")
        # Pre-write an old file to verify it gets replaced atomically
        with open(path, "w") as f:
            json.dump({"old": True}, f)
        new_factors = {"global": 0.95, "sample_count": 10, "status": "active"}
        cs.write_factors(path, new_factors)
        # Old content should be replaced
        with open(path) as f:
            result = json.load(f)
        assert result["global"] == 0.95
        assert "old" not in result

    def test_write_factors_creates_dirs(self, tmp_path):
        """write_factors creates parent directories if absent."""
        cs = load_calibration_store()
        nested = str(tmp_path / "deep" / "nested" / "factors.json")
        cs.write_factors(nested, {"status": "collecting"})
        assert os.path.exists(nested)

    def test_write_factors_valid_json(self, tmp_path):
        """Output file is parseable JSON."""
        cs = load_calibration_store()
        path = str(tmp_path / "factors.json")
        factors = {"global": 1.1, "sample_count": 7, "status": "active"}
        cs.write_factors(path, factors)
        with open(path) as f:
            result = json.load(f)
        assert result["global"] == 1.1

    # -----------------------------------------------------------------------
    # CLI: append-history command
    # -----------------------------------------------------------------------

    def test_cli_append_history(self, tmp_path):
        """CLI append-history writes record to history file."""
        if not CALIBRATION_STORE_PY.exists():
            pytest.skip("calibration_store.py not yet implemented")
        history_path = str(tmp_path / "history.jsonl")
        factors_path = str(tmp_path / "factors.json")
        record = make_record(ratio=1.0)
        result = subprocess.run(
            [
                sys.executable,
                str(CALIBRATION_STORE_PY),
                "append-history",
                "--history", history_path,
                "--factors", factors_path,
                "--record", json.dumps(record),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert os.path.exists(history_path)
        with open(history_path) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["ratio"] == 1.0

    def test_cli_append_history_triggers_factor_recompute(self, tmp_path):
        """After appending 3+ records via CLI, factors.json is created."""
        if not CALIBRATION_STORE_PY.exists():
            pytest.skip("calibration_store.py not yet implemented")
        if not UPDATE_FACTORS_PY.exists():
            pytest.skip("update-factors.py not found")
        history_path = str(tmp_path / "history.jsonl")
        factors_path = str(tmp_path / "factors.json")
        # Append 3 records (minimum for active status)
        for ratio in [0.9, 1.0, 1.1]:
            record = make_record(ratio=ratio)
            subprocess.run(
                [
                    sys.executable,
                    str(CALIBRATION_STORE_PY),
                    "append-history",
                    "--history", history_path,
                    "--factors", factors_path,
                    "--record", json.dumps(record),
                ],
                capture_output=True,
                text=True,
            )
        # After 3 records, factors.json should exist (update-factors.py was called)
        assert os.path.exists(factors_path), \
            "factors.json should exist after append-history triggered factor recompute"
        with open(factors_path) as f:
            factors = json.load(f)
        assert factors.get("sample_count", 0) >= 3
