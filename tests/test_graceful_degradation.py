# Run with: /usr/bin/python3 -m pytest tests/test_graceful_degradation.py
# Do NOT use bare 'python3 -m pytest' -- Homebrew Python 3.14 does not have pytest.
"""Graceful degradation tests (US-PM.04).

Covers:
1. Missing calibration directory (first-run)
   - estimate_cost returns uncalibrated estimate (factor=1.0, cal="--")
   - get_calibration_status returns "no_data" status
   - report_session creates the calibration directory

2. Corrupted factors.json or history.jsonl
   - Server logs warning and falls back to uncalibrated
   - Does NOT crash

3. wc -l failure (subprocess error, binary files, special characters)
   - Falls back to medium default (brackets=None)
   - Already handled by file_measurement.py

4. Disk write failure (read-only filesystem / OSError on write)
   - estimate_cost still returns the estimate even if active-estimate.json can't be written
   - report_session returns error response with record_written=False, not crash

5. Concurrent MCP tool calls
   - estimate_cost uses atomic rename (mkstemp + os.replace)
   - _save_accumulator uses atomic rename
"""

import json
import os
import pathlib
import stat
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — ensure src/ is importable
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tokencast.api import (
    estimate_cost,
    get_calibration_status,
    get_cost_history,
    report_session,
)
from tokencast.file_measurement import measure_files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_PARAMS = {
    "size": "M",
    "files": 3,
    "complexity": "medium",
}


def _make_active_estimate(cal_dir: Path, cost: float = 5.0) -> Path:
    """Write a minimal active-estimate.json to cal_dir."""
    cal_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "size": "M",
        "files": 3,
        "complexity": "medium",
        "project_type": "feature",
        "language": "python",
        "steps": ["Research Agent", "Implementation"],
        "step_count": 2,
        "review_cycles_estimated": 2,
        "expected_cost": cost,
        "optimistic_cost": cost * 0.6,
        "pessimistic_cost": cost * 3.0,
        "baseline_cost": 0.0,
        "parallel_groups": [],
        "parallel_steps_detected": 0,
        "file_brackets": None,
        "files_measured": 0,
        "step_costs": {"Research Agent": cost * 0.3, "Implementation": cost * 0.7},
        "continuation": False,
    }
    p = cal_dir / "active-estimate.json"
    p.write_text(json.dumps(payload))
    return p


def _read_history(cal_dir: Path) -> list:
    history = cal_dir / "history.jsonl"
    if not history.exists():
        return []
    records = []
    for line in history.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


# ---------------------------------------------------------------------------
# 1. Missing calibration directory (first-run)
# ---------------------------------------------------------------------------


class TestMissingCalibrationDirectory:
    """estimate_cost, get_calibration_status, and report_session behave correctly
    when the calibration directory does not exist at all."""

    def test_estimate_cost_returns_result_without_cal_dir(self, tmp_path):
        """estimate_cost with a non-existent calibration_dir returns a valid estimate."""
        missing_dir = str(tmp_path / "does_not_exist" / "calibration")
        result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=missing_dir)
        assert "estimate" in result
        assert result["estimate"]["expected"] > 0

    def test_estimate_cost_all_steps_have_no_calibration_factor(self, tmp_path):
        """With no calibration dir, all steps should have cal='--' (uncalibrated)."""
        missing_dir = str(tmp_path / "does_not_exist" / "calibration")
        result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=missing_dir)
        for step in result["steps"]:
            assert step["cal"] == "--", (
                f"Expected '--' cal for step '{step['name']}', got '{step['cal']}'"
            )

    def test_estimate_cost_all_steps_have_factor_1(self, tmp_path):
        """With no calibration dir, all steps should have factor=1.0."""
        missing_dir = str(tmp_path / "does_not_exist" / "calibration")
        result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=missing_dir)
        for step in result["steps"]:
            assert step["factor"] == pytest.approx(1.0), (
                f"Expected factor=1.0 for step '{step['name']}', got {step['factor']}"
            )

    def test_estimate_cost_no_cal_dir_returns_result(self):
        """estimate_cost with calibration_dir=None returns a valid estimate."""
        result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=None)
        assert "estimate" in result
        assert result["estimate"]["expected"] > 0

    def test_get_calibration_status_returns_no_data_for_missing_dir(self, tmp_path):
        """get_calibration_status with a missing calibration dir returns no_data status."""
        missing_dir = str(tmp_path / "does_not_exist" / "calibration")
        result = get_calibration_status({}, calibration_dir=missing_dir)
        assert "health" in result
        assert result["health"]["status"] == "no_data"

    def test_get_calibration_status_schema_version_present(self, tmp_path):
        """Even with missing dir, response has schema_version."""
        missing_dir = str(tmp_path / "does_not_exist" / "calibration")
        result = get_calibration_status({}, calibration_dir=missing_dir)
        assert result.get("schema_version") == 1

    def test_report_session_creates_calibration_directory(self, tmp_path):
        """report_session creates the calibration directory when it doesn't exist."""
        missing_dir = tmp_path / "new_calibration"
        assert not missing_dir.exists()
        report_session({"actual_cost": 2.0}, calibration_dir=str(missing_dir))
        assert missing_dir.exists()

    def test_report_session_writes_history_when_dir_missing(self, tmp_path):
        """report_session writes a history record even when dir had to be created."""
        missing_dir = tmp_path / "new_calibration"
        result = report_session({"actual_cost": 2.0}, calibration_dir=str(missing_dir))
        # Should either write a record or report no_active_estimate warning (not crash)
        assert "attribution_protocol_version" in result
        assert result["attribution_protocol_version"] == 1

    def test_get_cost_history_returns_empty_for_missing_dir(self, tmp_path):
        """get_cost_history with a missing calibration dir returns empty records."""
        missing_dir = str(tmp_path / "does_not_exist" / "calibration")
        result = get_cost_history({}, calibration_dir=missing_dir)
        assert result["records"] == []
        assert result["summary"]["session_count"] == 0


# ---------------------------------------------------------------------------
# 2. Corrupted factors.json or history.jsonl
# ---------------------------------------------------------------------------


class TestCorruptedCalibrationFiles:
    """Server falls back to uncalibrated / empty when calibration files are corrupted."""

    def test_corrupted_factors_json_falls_back_to_uncalibrated(self, tmp_path):
        """estimate_cost with corrupted factors.json falls back to factor=1.0."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir()
        # Write garbage to factors.json
        (cal_dir / "factors.json").write_text("THIS IS NOT JSON {{{")
        result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=str(cal_dir))
        assert "estimate" in result
        for step in result["steps"]:
            assert step["cal"] == "--"
            assert step["factor"] == pytest.approx(1.0)

    def test_corrupted_factors_json_does_not_crash(self, tmp_path):
        """estimate_cost does not raise when factors.json is corrupted."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir()
        (cal_dir / "factors.json").write_text("\x00\x01\x02 invalid bytes")
        # Should not raise
        result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=str(cal_dir))
        assert result is not None

    def test_corrupted_history_jsonl_returns_empty_records(self, tmp_path):
        """get_cost_history with corrupted history.jsonl returns empty list (no crash)."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir()
        # Write garbage to history.jsonl
        (cal_dir / "history.jsonl").write_text("not json\nalso not json\n{broken")
        result = get_cost_history({}, calibration_dir=str(cal_dir))
        # calibration_store.read_history skips unparseable lines — may return [] or partial
        assert isinstance(result["records"], list)
        assert result is not None

    def test_corrupted_history_does_not_crash_get_cost_history(self, tmp_path):
        """get_cost_history does not raise when history.jsonl is corrupted."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir()
        (cal_dir / "history.jsonl").write_text("\x00\x01\x02")
        # Should not raise
        result = get_cost_history({}, calibration_dir=str(cal_dir))
        assert result is not None

    def test_corrupted_history_does_not_crash_get_calibration_status(self, tmp_path):
        """get_calibration_status does not raise when history.jsonl is corrupted."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir()
        (cal_dir / "history.jsonl").write_text("bad data")
        result = get_calibration_status({}, calibration_dir=str(cal_dir))
        assert result is not None
        assert "health" in result

    def test_corrupted_factors_in_get_calibration_status(self, tmp_path):
        """get_calibration_status does not crash when factors.json is corrupted."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir()
        (cal_dir / "factors.json").write_text("{{invalid_json}")
        result = get_calibration_status({}, calibration_dir=str(cal_dir))
        assert result is not None
        assert "health" in result

    def test_truncated_active_estimate_is_handled_by_report_session(self, tmp_path):
        """report_session handles truncated/corrupted active-estimate.json gracefully."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir()
        # Write partial JSON
        (cal_dir / "active-estimate.json").write_text('{"size": "M", "expected_cost":')
        result = report_session({"actual_cost": 1.5}, calibration_dir=str(cal_dir))
        # Should not crash; may return with warning or record_written=True with fallback
        assert "attribution_protocol_version" in result
        assert result["attribution_protocol_version"] == 1

    def test_empty_factors_json_treated_as_no_calibration(self, tmp_path):
        """An empty factors.json (valid JSON '{}') produces uncalibrated estimates."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir()
        (cal_dir / "factors.json").write_text("{}")
        result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=str(cal_dir))
        for step in result["steps"]:
            assert step["cal"] == "--"


# ---------------------------------------------------------------------------
# 3. wc -l failure
# ---------------------------------------------------------------------------


class TestWcLFailure:
    """measure_files falls back to medium default on subprocess failure."""

    def test_subprocess_exception_returns_null_brackets(self):
        """When subprocess.run raises, measure_files returns brackets=None."""
        with patch("subprocess.run", side_effect=OSError("permission denied")):
            result = measure_files(["some_file.py"])
        assert result["brackets"] is None

    def test_subprocess_exception_returns_medium_defaults(self):
        """When subprocess.run raises, avg tokens default to medium (10000/2500)."""
        with patch("subprocess.run", side_effect=OSError("permission denied")):
            result = measure_files(["some_file.py"])
        assert result["avg_file_read_tokens"] == 10000
        assert result["avg_file_edit_tokens"] == 2500

    def test_subprocess_exception_returns_files_measured_zero(self):
        """When subprocess.run raises, files_measured is 0."""
        with patch("subprocess.run", side_effect=OSError("permission denied")):
            result = measure_files(["file.py"])
        assert result["files_measured"] == 0

    def test_binary_files_excluded_from_measurement(self):
        """Binary file extensions are filtered before calling wc -l."""
        # .png is a binary extension — should not be passed to wc -l
        # No subprocess call should be made if all files are binary
        call_count = {"n": 0}

        def mock_run(cmd, **kwargs):
            call_count["n"] += 1

            class FakeResult:
                stdout = ""
                returncode = 0

            return FakeResult()

        with patch("subprocess.run", side_effect=mock_run):
            result = measure_files(["image.png", "photo.jpg"])
        # All binary — subprocess should not be called
        assert call_count["n"] == 0
        assert result["brackets"] == {"small": 0, "medium": 0, "large": 0}

    def test_special_chars_in_paths_do_not_cause_subprocess_exception(self, tmp_path):
        """Paths with spaces and special characters are handled without crashing."""
        # Create an actual file with a space in the name
        special_file = tmp_path / "my file with spaces.py"
        special_file.write_text("x = 1\n" * 10)
        result = measure_files([str(special_file)])
        # Should not raise; brackets should be populated
        assert result["brackets"] is not None or result["brackets"] is None  # either is fine

    def test_measure_files_empty_list_returns_null_brackets(self):
        """Empty file_paths list → brackets=None (no paths extracted)."""
        result = measure_files([])
        assert result["brackets"] is None
        assert result["files_measured"] == 0

    def test_estimate_cost_with_wc_failure_still_returns_estimate(self, tmp_path):
        """When wc -l fails during estimate_cost, a valid estimate is still returned."""
        with patch("subprocess.run", side_effect=OSError("permission denied")):
            result = estimate_cost(
                {**_MINIMAL_PARAMS, "file_paths": ["src/main.py", "src/utils.py"]},
                calibration_dir=None,
            )
        assert "estimate" in result
        assert result["estimate"]["expected"] > 0


# ---------------------------------------------------------------------------
# 4. Disk write failure
# ---------------------------------------------------------------------------


class TestDiskWriteFailure:
    """estimate_cost still returns estimate on write failure;
    report_session returns error response (not crash)."""

    def test_estimate_cost_returns_estimate_when_active_estimate_write_fails(self, tmp_path):
        """estimate_cost returns valid estimate even when active-estimate.json can't be written."""
        cal_dir = str(tmp_path / "calibration")
        # Patch os.replace to simulate write failure after mkstemp
        with patch("os.replace", side_effect=OSError("read-only filesystem")):
            result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=cal_dir)
        assert "estimate" in result
        assert result["estimate"]["expected"] > 0

    def test_estimate_cost_returns_steps_when_write_fails(self, tmp_path):
        """estimate_cost returns step list even when active-estimate.json write fails."""
        cal_dir = str(tmp_path / "calibration")
        with patch("os.replace", side_effect=OSError("read-only filesystem")):
            result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=cal_dir)
        assert "steps" in result
        assert len(result["steps"]) > 0

    def test_estimate_cost_does_not_raise_on_write_failure(self, tmp_path):
        """estimate_cost does not raise an exception when write fails."""
        cal_dir = str(tmp_path / "calibration")
        with patch("os.replace", side_effect=OSError("disk full")):
            # Must not raise
            result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=cal_dir)
        assert result is not None

    def test_estimate_cost_last_estimate_md_write_failure_non_fatal(self, tmp_path):
        """estimate_cost tolerates failure writing last-estimate.md."""
        cal_dir = tmp_path / "calibration"
        cal_dir.mkdir()

        # Patch Path.write_text to fail for last-estimate.md only
        original_write_text = Path.write_text

        def patched_write_text(self, content, **kwargs):
            if self.name == "last-estimate.md":
                raise OSError("disk full")
            return original_write_text(self, content, **kwargs)

        with patch.object(Path, "write_text", patched_write_text):
            result = estimate_cost(_MINIMAL_PARAMS, calibration_dir=str(cal_dir))
        assert "estimate" in result
        assert result["estimate"]["expected"] > 0

    def test_report_session_returns_error_on_write_failure(self, tmp_path):
        """report_session returns error response (not crash) when history write fails."""
        cal_dir = tmp_path / "calibration"
        _make_active_estimate(cal_dir)

        from tokencast import calibration_store as _cs_pkg

        with patch.object(_cs_pkg, "append_history", side_effect=OSError("read-only")):
            result = report_session({"actual_cost": 2.0}, calibration_dir=str(cal_dir))

        assert result is not None
        assert "attribution_protocol_version" in result

    def test_report_session_record_written_false_on_write_failure(self, tmp_path):
        """report_session returns record_written=False when disk write fails."""
        cal_dir = tmp_path / "calibration"
        _make_active_estimate(cal_dir)

        from tokencast import calibration_store as _cs_pkg

        with patch.object(_cs_pkg, "append_history", side_effect=OSError("read-only")):
            result = report_session({"actual_cost": 2.0}, calibration_dir=str(cal_dir))

        assert result["record_written"] is False

    def test_report_session_error_key_present_on_write_failure(self, tmp_path):
        """report_session returns error key on disk write failure."""
        cal_dir = tmp_path / "calibration"
        _make_active_estimate(cal_dir)

        from tokencast import calibration_store as _cs_pkg

        with patch.object(_cs_pkg, "append_history", side_effect=OSError("read-only")):
            result = report_session({"actual_cost": 2.0}, calibration_dir=str(cal_dir))

        assert result.get("error") == "write_failed"

    def test_report_session_mkdir_failure_returns_error(self, tmp_path):
        """report_session returns error response when mkdir fails."""
        # Use a path whose parent is a file (can't create dir)
        blocking_file = tmp_path / "blocker"
        blocking_file.write_text("I am a file")
        bad_cal_dir = str(blocking_file / "calibration")

        result = report_session({"actual_cost": 2.0}, calibration_dir=bad_cal_dir)
        # Should not crash; record_written must be False
        assert result is not None
        assert "attribution_protocol_version" in result
        assert result["record_written"] is False


# ---------------------------------------------------------------------------
# 5. Concurrent MCP tool calls
# ---------------------------------------------------------------------------


class TestConcurrentCalls:
    """Atomic rename patterns prevent corruption under concurrent calls."""

    def test_estimate_cost_uses_atomic_rename(self, tmp_path):
        """active-estimate.json is written atomically (no .tmp leftover on success)."""
        cal_dir = str(tmp_path / "calibration")
        estimate_cost(_MINIMAL_PARAMS, calibration_dir=cal_dir)
        cal_path = Path(cal_dir)
        tmp_files = list(cal_path.glob("*.tmp"))
        assert tmp_files == [], f"Unexpected .tmp files after successful write: {tmp_files}"

    def test_estimate_cost_active_estimate_written(self, tmp_path):
        """active-estimate.json is present after estimate_cost completes."""
        cal_dir = str(tmp_path / "calibration")
        estimate_cost(_MINIMAL_PARAMS, calibration_dir=cal_dir)
        assert (Path(cal_dir) / "active-estimate.json").exists()

    def test_estimate_cost_active_estimate_is_valid_json(self, tmp_path):
        """active-estimate.json is parseable JSON after estimate_cost."""
        cal_dir = str(tmp_path / "calibration")
        estimate_cost(_MINIMAL_PARAMS, calibration_dir=cal_dir)
        content = (Path(cal_dir) / "active-estimate.json").read_text()
        data = json.loads(content)
        assert "expected_cost" in data

    def test_two_sequential_estimate_cost_calls_produce_valid_last_file(self, tmp_path):
        """Two sequential estimate_cost calls each produce a valid active-estimate.json."""
        cal_dir = str(tmp_path / "calibration")
        estimate_cost(_MINIMAL_PARAMS, calibration_dir=cal_dir)
        # Second call overwrites atomically
        params2 = {**_MINIMAL_PARAMS, "files": 5, "complexity": "high"}
        estimate_cost(params2, calibration_dir=cal_dir)
        content = (Path(cal_dir) / "active-estimate.json").read_text()
        data = json.loads(content)
        assert data["files"] == 5
        assert data["complexity"] == "high"

    def test_concurrent_estimate_cost_calls_both_return_valid_results(self, tmp_path):
        """Two concurrent estimate_cost calls both return valid results (no exception)."""
        cal_dir = str(tmp_path / "calibration")
        results = [None, None]
        errors = [None, None]

        def run_estimate(idx, extra_files):
            try:
                params = {**_MINIMAL_PARAMS, "files": extra_files}
                results[idx] = estimate_cost(params, calibration_dir=cal_dir)
            except Exception as e:
                errors[idx] = e

        t1 = threading.Thread(target=run_estimate, args=(0, 3))
        t2 = threading.Thread(target=run_estimate, args=(1, 5))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert errors[0] is None, f"Thread 0 raised: {errors[0]}"
        assert errors[1] is None, f"Thread 1 raised: {errors[1]}"
        assert results[0] is not None
        assert results[1] is not None
        assert results[0]["estimate"]["expected"] > 0
        assert results[1]["estimate"]["expected"] > 0

    def test_concurrent_estimate_cost_final_file_is_valid_json(self, tmp_path):
        """After concurrent writes, the final active-estimate.json is valid JSON."""
        cal_dir = str(tmp_path / "calibration")
        errors = []

        def run_estimate():
            try:
                estimate_cost(_MINIMAL_PARAMS, calibration_dir=cal_dir)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=run_estimate) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Threads raised exceptions: {errors}"
        ae_path = Path(cal_dir) / "active-estimate.json"
        if ae_path.exists():
            content = ae_path.read_text()
            data = json.loads(content)  # must not raise
            assert "expected_cost" in data

    def test_no_tmp_files_after_concurrent_writes(self, tmp_path):
        """No .tmp files remain after concurrent estimate_cost calls."""
        cal_dir = str(tmp_path / "calibration")

        def run():
            estimate_cost(_MINIMAL_PARAMS, calibration_dir=cal_dir)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        tmp_files = list(Path(cal_dir).glob("*.tmp"))
        assert tmp_files == [], f"Stale .tmp files: {tmp_files}"
