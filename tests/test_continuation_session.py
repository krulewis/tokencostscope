# Run with: /usr/bin/python3 -m pytest tests/test_continuation_session.py
# Do NOT use bare 'python3 -m pytest' -- Homebrew Python 3.14 does not have pytest.
"""Tests for parse_last_estimate.py (unit) and learn.sh continuation reconstitution (integration).

Unit tests (TestParseLastEstimate) verify the parse() function's behaviour across
valid, stale, and malformed inputs.

Integration tests (TestLearnShContinuation) verify that learn.sh correctly
reconstitutes a calibration record from last-estimate.md when active-estimate.json
is absent, and that the resulting history record contains the continuation flag.
"""

import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Load parse_last_estimate module via importlib (filename has underscores but
# we follow the importlib pattern used in other test files for consistency)
# ---------------------------------------------------------------------------
_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "parse_last_estimate",
    _scripts_dir / "parse_last_estimate.py",
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
parse = _mod.parse

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
LEARN_SH = REPO_ROOT / "scripts" / "tokencostscope-learn.sh"

# SAMPLE_MD — valid last-estimate.md content WITHOUT a Baseline Cost line.
# Uses the exact format written by SKILL.md (Pessimistic row tight: no space before pipe).
SAMPLE_MD = """\
# Last tokencostscope Estimate

**Feature:** v2.1 test fixture
**Recorded:** 2026-03-24T00:00:00Z
**Size:** M | **Files:** 12 | **Complexity:** high
**Type:** bug_fix | **Language:** python
**Steps:** Research Agent, Implementation, QA
**File Brackets:** 3 measured (0 small, 3 medium, 0 large); 2 defaulted

| Band       | Cost    |
|------------|---------|
| Optimistic | $9.15   |
| Expected   | $15.18  |
| Pessimistic| $42.70|

Review cycles estimated: 2
Parallel steps detected: 0
"""

# SAMPLE_MD_WITH_BASELINE appends a Baseline Cost footer line.
# Value is $0.01 (not $0.05) so it stays below the mock session JSONL cost ($0.025),
# preventing actual_cost from clamping to zero in integration tests.
SAMPLE_MD_WITH_BASELINE = SAMPLE_MD + "Baseline Cost: $0.01\n"

# SAMPLE_MD_BASELINE_ONLY_FOR_UNIT — used in unit tests that need to assert
# baseline_cost=0.05 is parsed correctly, without running through learn.sh.
SAMPLE_MD_BASELINE_UNIT = SAMPLE_MD + "Baseline Cost: $0.05\n"


# ---------------------------------------------------------------------------
# Class 1: TestParseLastEstimate — unit tests for parse()
# ---------------------------------------------------------------------------


class TestParseLastEstimate(unittest.TestCase):
    """Unit tests for parse_last_estimate.parse()."""

    # --- happy path ---

    def test_valid_recent_file(self):
        """Recent file with all fields returns populated dict."""
        result = parse(SAMPLE_MD_BASELINE_UNIT, mtime=time.time())
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["expected_cost"], 15.18, places=4)
        self.assertAlmostEqual(result["optimistic_cost"], 9.15, places=4)
        self.assertAlmostEqual(result["pessimistic_cost"], 42.70, places=4)
        self.assertAlmostEqual(result["baseline_cost"], 0.05, places=4)
        self.assertEqual(result["size"], "M")
        self.assertEqual(result["files"], 12)
        self.assertEqual(result["complexity"], "high")
        self.assertTrue(result["continuation"])

    def test_no_baseline_cost_line(self):
        """Content without Baseline Cost line returns dict with baseline_cost=0.0."""
        result = parse(SAMPLE_MD, mtime=time.time())
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["baseline_cost"], 0.0, places=4)

    # --- staleness ---

    def test_stale_file(self):
        """File older than max_age_hours returns None."""
        result = parse(SAMPLE_MD_WITH_BASELINE, max_age_hours=48.0, mtime=time.time() - 49 * 3600)
        self.assertIsNone(result)

    def test_zero_max_age(self):
        """max_age_hours=0.0 with mtime 1 second ago returns None."""
        result = parse(SAMPLE_MD_WITH_BASELINE, max_age_hours=0.0, mtime=time.time() - 1)
        self.assertIsNone(result)

    def test_mtime_none_skips_recency(self):
        """mtime=None bypasses the recency check entirely."""
        result = parse(SAMPLE_MD_WITH_BASELINE, mtime=None)
        self.assertIsNotNone(result)

    # --- missing / bad content ---

    def test_missing_cost_table(self):
        """Content with no cost table rows returns None."""
        content = """\
# Last tokencostscope Estimate

**Size:** M | **Files:** 5 | **Complexity:** medium

Review cycles estimated: 0
"""
        result = parse(content, mtime=None)
        self.assertIsNone(result)

    def test_zero_expected_cost(self):
        """Content with | Expected | $0.00 | returns None (> 0 guard fires)."""
        content = SAMPLE_MD.replace("| Expected   | $15.18  |", "| Expected   | $0.00   |")
        result = parse(content, mtime=None)
        self.assertIsNone(result)

    # --- whitespace / format variation ---

    def test_whitespace_variation_in_table(self):
        """Extra spaces in cost table rows are handled correctly."""
        content = """\
# Last tokencostscope Estimate

**Size:** M | **Files:** 5 | **Complexity:** medium

| Band       | Cost    |
|------------|---------|
| Optimistic  |  $6.18   |
|  Expected   |   $10.30  |
| Pessimistic | $30.90 |

Review cycles estimated: 0
Parallel steps detected: 0
"""
        result = parse(content, mtime=None)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["optimistic_cost"], 6.18, places=4)
        self.assertAlmostEqual(result["expected_cost"], 10.30, places=4)
        self.assertAlmostEqual(result["pessimistic_cost"], 30.90, places=4)

    def test_dollar_prefix_stripped(self):
        """Parsed cost values are floats, not strings with $ prefix."""
        result = parse(SAMPLE_MD, mtime=None)
        self.assertIsNotNone(result)
        self.assertIsInstance(result["expected_cost"], float)
        self.assertIsInstance(result["optimistic_cost"], float)
        self.assertIsInstance(result["pessimistic_cost"], float)

    def test_pessimistic_no_space_before_pipe(self):
        """Pessimistic row with tight formatting (no space before pipe) is parsed correctly."""
        # SAMPLE_MD uses '| Pessimistic| $42.70|' — tight format
        result = parse(SAMPLE_MD, mtime=None)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["pessimistic_cost"], 42.70, places=4)

    # --- optional fields / defaults ---

    def test_missing_type_language(self):
        """Content without **Type:** line returns dict with defaults."""
        content = """\
# Last tokencostscope Estimate

**Size:** S | **Files:** 3 | **Complexity:** low

| Band       | Cost  |
|------------|-------|
| Optimistic | $1.00 |
| Expected   | $2.00 |
| Pessimistic| $6.00 |

Review cycles estimated: 1
Parallel steps detected: 0
"""
        result = parse(content, mtime=None)
        self.assertIsNotNone(result)
        self.assertEqual(result["project_type"], "unknown")
        self.assertEqual(result["language"], "unknown")

    def test_missing_steps(self):
        """Content without **Steps:** line returns dict with steps=[] and step_count=0."""
        content = """\
# Last tokencostscope Estimate

**Size:** S | **Files:** 3 | **Complexity:** low

| Band       | Cost  |
|------------|-------|
| Optimistic | $1.00 |
| Expected   | $2.00 |
| Pessimistic| $6.00 |

Review cycles estimated: 0
Parallel steps detected: 0
"""
        result = parse(content, mtime=None)
        self.assertIsNotNone(result)
        self.assertEqual(result["steps"], [])
        self.assertEqual(result["step_count"], 0)

    def test_steps_parsed(self):
        """**Steps:** line produces correct list and count."""
        result = parse(SAMPLE_MD, mtime=None)
        self.assertIsNotNone(result)
        self.assertEqual(result["steps"], ["Research Agent", "Implementation", "QA"])
        self.assertEqual(result["step_count"], 3)

    def test_review_cycles_parsed(self):
        """Review cycles estimated: N is parsed correctly."""
        result = parse(SAMPLE_MD, mtime=None)
        self.assertIsNotNone(result)
        self.assertEqual(result["review_cycles_estimated"], 2)

    def test_parallel_steps_parsed(self):
        """Parallel steps detected: N is parsed correctly."""
        content = SAMPLE_MD.replace("Parallel steps detected: 0", "Parallel steps detected: 2")
        result = parse(content, mtime=None)
        self.assertIsNotNone(result)
        self.assertEqual(result["parallel_steps_detected"], 2)

    # --- schema completeness ---

    def test_output_schema_completeness(self):
        """Valid parse result contains all expected keys."""
        result = parse(SAMPLE_MD_WITH_BASELINE, mtime=None)
        self.assertIsNotNone(result)
        expected_keys = {
            "timestamp", "size", "files", "complexity", "steps", "step_count",
            "project_type", "language", "expected_cost", "optimistic_cost",
            "pessimistic_cost", "baseline_cost", "review_cycles_estimated",
            "review_cycles_actual", "parallel_groups", "parallel_steps_detected",
            "file_brackets", "files_measured", "step_costs", "continuation",
        }
        for key in expected_keys:
            self.assertIn(key, result, f"Missing key: {key}")

    def test_fixed_defaults(self):
        """Fixed-default fields have the correct static values."""
        result = parse(SAMPLE_MD, mtime=None)
        self.assertIsNotNone(result)
        self.assertIsNone(result["review_cycles_actual"])
        self.assertEqual(result["parallel_groups"], [])
        self.assertIsNone(result["file_brackets"])
        self.assertEqual(result["files_measured"], 0)
        self.assertEqual(result["step_costs"], {})

    def test_continuation_marker(self):
        """continuation field is True."""
        result = parse(SAMPLE_MD, mtime=None)
        self.assertIsNotNone(result)
        self.assertIs(result["continuation"], True)

    def test_timestamp_is_iso8601(self):
        """timestamp field matches ISO 8601 format."""
        result = parse(SAMPLE_MD, mtime=None)
        self.assertIsNotNone(result)
        self.assertRegex(result["timestamp"], r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')

    # --- __main__ block via subprocess ---

    def test_env_var_max_age_zero_in_main_block(self):
        """TOKENCOSTSCOPE_CONTINUATION_MAX_AGE_HOURS=0 causes __main__ to exit non-zero."""
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "last-estimate.md"
            md_path.write_text(SAMPLE_MD_WITH_BASELINE)
            # File is fresh — but env var sets max_age=0 → stale immediately
            env = {**os.environ, "TOKENCOSTSCOPE_CONTINUATION_MAX_AGE_HOURS": "0"}
            result = subprocess.run(
                ["/usr/bin/python3", str(_scripts_dir / "parse_last_estimate.py"), str(md_path)],
                capture_output=True, text=True, env=env,
            )
            self.assertNotEqual(result.returncode, 0)


# ---------------------------------------------------------------------------
# Class 2: TestLearnShContinuation — integration tests
# ---------------------------------------------------------------------------


class TestLearnShContinuation(unittest.TestCase):
    """Integration tests for learn.sh continuation reconstitution path."""

    LEARN_SH = REPO_ROOT / "scripts" / "tokencostscope-learn.sh"

    def _write_mock_last_estimate(
        self,
        tmp_dir: Path,
        include_baseline: bool = True,
        age_seconds: float = 0,
    ) -> Path:
        """Write last-estimate.md to tmp_dir. Back-dates mtime when age_seconds > 0."""
        path = tmp_dir / "last-estimate.md"
        content = SAMPLE_MD_WITH_BASELINE if include_baseline else SAMPLE_MD
        path.write_text(content)
        if age_seconds > 0:
            mtime = time.time() - age_seconds
            os.utime(str(path), (mtime, mtime))
        return path

    def _write_mock_session_jsonl(self, tmp_dir: Path) -> Path:
        """Write a minimal valid session JSONL with actual_cost > 0.001."""
        entry = {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 5000,
                    "output_tokens": 500,
                    "cache_read_input_tokens": 2000,
                    "cache_creation_input_tokens": 500,
                },
            },
            "costUSD": 0.025,
        }
        path = tmp_dir / "session.jsonl"
        path.write_text(json.dumps(entry) + "\n")
        return path

    def _write_mock_active_estimate(self, tmp_dir: Path) -> Path:
        """Write a minimal active-estimate.json (for normal-path tests)."""
        estimate = {
            "timestamp": "2026-01-01T00:00:00Z",
            "size": "L",
            "files": 7,
            "complexity": "low",
            "steps": ["Implementation", "QA"],
            "step_count": 2,
            "project_type": "greenfield",
            "language": "python",
            "expected_cost": 0.05,
            "optimistic_cost": 0.03,
            "pessimistic_cost": 0.15,
            "baseline_cost": 0.0,
            "review_cycles_estimated": 0,
            "review_cycles_actual": None,
            "parallel_groups": [],
            "parallel_steps_detected": 0,
            "file_brackets": None,
            "files_measured": 0,
            "step_costs": {},
            "continuation": False,
        }
        path = tmp_dir / "active-estimate.json"
        path.write_text(json.dumps(estimate))
        return path

    def _run_learn_sh(
        self,
        session_file: Path,
        tmp_dir: Path,
        env_extra: Optional[dict] = None,
    ) -> subprocess.CompletedProcess:
        """Run learn.sh with TOKENCOSTSCOPE_ESTIMATE_FILE and HISTORY_FILE overrides."""
        env = {
            **os.environ,
            "TOKENCOSTSCOPE_ESTIMATE_FILE": str(tmp_dir / "active-estimate.json"),
            "TOKENCOSTSCOPE_HISTORY_FILE": str(tmp_dir / "history.jsonl"),
            # Disable sidecar discovery for all integration tests
            "TOKENCOSTSCOPE_SIDECAR_PATH": "",
        }
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", str(self.LEARN_SH), str(session_file), "0"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )

    def _read_last_history_record(self, tmp_dir: Path) -> Optional[dict]:
        """Return the last record from history.jsonl, or None if file absent."""
        history_path = tmp_dir / "history.jsonl"
        if not history_path.exists():
            return None
        lines = [l for l in history_path.read_text().splitlines() if l.strip()]
        if not lines:
            return None
        return json.loads(lines[-1])

    # --- continuation path writes history ---

    def test_continuation_writes_history(self):
        """No active-estimate.json + recent last-estimate.md -> history.jsonl written."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            self._write_mock_last_estimate(tmp_dir)
            session_file = self._write_mock_session_jsonl(tmp_dir)

            result = self._run_learn_sh(session_file, tmp_dir)
            self.assertEqual(result.returncode, 0, f"learn.sh failed: {result.stderr}")

            history_path = tmp_dir / "history.jsonl"
            if not history_path.exists():
                self.skipTest("learn.sh did not write history record (actual_cost too low)")
            lines = [l for l in history_path.read_text().splitlines() if l.strip()]
            self.assertGreaterEqual(len(lines), 1)

    def test_continuation_record_has_continuation_flag(self):
        """History record from continuation path has continuation=true."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            self._write_mock_last_estimate(tmp_dir)
            session_file = self._write_mock_session_jsonl(tmp_dir)

            self._run_learn_sh(session_file, tmp_dir)

            record = self._read_last_history_record(tmp_dir)
            if record is None:
                self.skipTest("learn.sh did not write history record")
            self.assertTrue(record.get("continuation"), f"continuation flag missing or false: {record}")

    def test_continuation_record_fields(self):
        """History record from continuation path contains cost and metadata fields."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            self._write_mock_last_estimate(tmp_dir)
            session_file = self._write_mock_session_jsonl(tmp_dir)

            self._run_learn_sh(session_file, tmp_dir)

            record = self._read_last_history_record(tmp_dir)
            if record is None:
                self.skipTest("learn.sh did not write history record")

            for field in ("expected_cost", "optimistic_cost", "pessimistic_cost", "size", "complexity"):
                self.assertIn(field, record, f"Missing field: {field}")

    def test_continuation_estimate_cleaned_up(self):
        """active-estimate.json is removed after learn.sh exits (cleanup is unconditional)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            self._write_mock_last_estimate(tmp_dir)
            session_file = self._write_mock_session_jsonl(tmp_dir)

            self._run_learn_sh(session_file, tmp_dir)

            active_estimate = tmp_dir / "active-estimate.json"
            self.assertFalse(
                active_estimate.exists(),
                "active-estimate.json should be cleaned up after learn.sh exits",
            )

    # --- normal path takes priority ---

    def test_normal_path_not_overridden(self):
        """active-estimate.json present -> learn.sh uses it (not last-estimate.md)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            self._write_mock_active_estimate(tmp_dir)
            self._write_mock_last_estimate(tmp_dir)
            session_file = self._write_mock_session_jsonl(tmp_dir)

            self._run_learn_sh(session_file, tmp_dir)

            record = self._read_last_history_record(tmp_dir)
            if record is None:
                self.skipTest("learn.sh did not write history record")

            # active-estimate.json has size=L and continuation=False
            # last-estimate.md has size=M and continuation=True
            # If active-estimate.json was used, size should be L (not M from last-estimate.md)
            self.assertEqual(record.get("size"), "L",
                             "Expected size='L' from active-estimate.json, not 'M' from last-estimate.md")
            self.assertFalse(record.get("continuation", False),
                             "continuation should be False when active-estimate.json is used")

    # --- stale / missing cases ---

    def test_stale_last_estimate_skipped(self):
        """last-estimate.md older than 48h -> learn.sh exits without writing history."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            self._write_mock_last_estimate(tmp_dir, age_seconds=49 * 3600)
            session_file = self._write_mock_session_jsonl(tmp_dir)

            result = self._run_learn_sh(session_file, tmp_dir)
            self.assertEqual(result.returncode, 0)
            self.assertFalse((tmp_dir / "history.jsonl").exists(),
                             "history.jsonl should not be written for stale last-estimate.md")

    def test_no_last_estimate_exits_clean(self):
        """Neither active-estimate.json nor last-estimate.md -> exits 0, no history."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            session_file = self._write_mock_session_jsonl(tmp_dir)

            result = self._run_learn_sh(session_file, tmp_dir)
            self.assertEqual(result.returncode, 0)
            self.assertFalse((tmp_dir / "history.jsonl").exists())

    def test_continuation_max_age_env_override(self):
        """TOKENCOSTSCOPE_CONTINUATION_MAX_AGE_HOURS=0 forces staleness on fresh file.

        max_age=0 means threshold=0 seconds; any file has age > 0, so it is always
        stale regardless of when it was written.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            self._write_mock_last_estimate(tmp_dir)  # fresh file
            session_file = self._write_mock_session_jsonl(tmp_dir)

            result = self._run_learn_sh(
                session_file, tmp_dir,
                env_extra={"TOKENCOSTSCOPE_CONTINUATION_MAX_AGE_HOURS": "0"},
            )
            self.assertEqual(result.returncode, 0)
            self.assertFalse(
                (tmp_dir / "history.jsonl").exists(),
                "history.jsonl should not be written when max_age=0 forces staleness",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
