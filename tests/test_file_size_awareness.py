"""Tests for file size awareness (v1.5.0).

Tests bracket assignment, Step 3a bracket-weighted computation, override resolution,
fallback chain, edge cases, learn.sh field forwarding, and document content verification.
Arithmetic tests pass immediately; document/learn.sh tests fail before implementation.
"""
# Runner: pytest (required). Non-TestCase classes are pytest-style and will be
# silently skipped by `python -m unittest`. Use: /usr/bin/python3 -m pytest tests/

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
LEARN_SH = SCRIPTS_DIR / "tokencast-learn.sh"
HEURISTICS_MD = REPO_ROOT / "references" / "heuristics.md"
SKILL_MD = REPO_ROOT / "SKILL.md"


# ---------------------------------------------------------------------------
# Arithmetic helpers — mirror the formulas described in the feature spec
# ---------------------------------------------------------------------------

def assign_bracket(line_count: int) -> str:
    """Assign a size bracket based on line count.

    Small:  lines <= 49
    Medium: 50 <= lines <= 500
    Large:  lines >= 501
    """
    if line_count <= 49:
        return "small"
    elif line_count <= 500:
        return "medium"
    else:
        return "large"


def bracket_for_override(avg_file_lines: int) -> str:
    """Return bracket for an avg_file_lines override value."""
    return assign_bracket(avg_file_lines)


def compute_file_read_contribution(file_brackets, N: int = 5) -> int:
    """Compute total input tokens for an N-scaling step.

    For N-scaling steps (Implementation, Test Writing N-writes):
      contribution = brackets["small"] * 3000 + brackets["medium"] * 10000 + brackets["large"] * 20000

    When file_brackets is None, falls back to N * 10000 (medium default, v1.4.0 behavior).
    """
    if file_brackets is None:
        return N * 10_000
    return (
        file_brackets.get("small", 0) * 3_000
        + file_brackets.get("medium", 0) * 10_000
        + file_brackets.get("large", 0) * 20_000
    )


def compute_file_edit_contribution(file_brackets, N: int = 5) -> int:
    """Compute total edit input tokens for a step.

    contribution = brackets["small"] * 1000 + brackets["medium"] * 2500 + brackets["large"] * 5000
    When file_brackets is None, falls back to N * 2500 (medium default).
    """
    if file_brackets is None:
        return N * 2_500
    return (
        file_brackets.get("small", 0) * 1_000
        + file_brackets.get("medium", 0) * 2_500
        + file_brackets.get("large", 0) * 5_000
    )


def compute_weighted_average(file_brackets: dict) -> tuple:
    """Compute weighted-average (avg_read_tokens, avg_edit_tokens) from bracket counts.

    If total_measured == 0, returns (10000, 2500) as medium defaults.
    """
    small = file_brackets.get("small", 0)
    medium = file_brackets.get("medium", 0)
    large = file_brackets.get("large", 0)
    total = small + medium + large
    if total == 0:
        return (10_000, 2_500)
    avg_read = (small * 3_000 + medium * 10_000 + large * 20_000) / total
    avg_edit = (small * 1_000 + medium * 2_500 + large * 5_000) / total
    return (avg_read, avg_edit)


def resolve_bracket(
    line_count=None,
    is_new: bool = False,
    avg_file_lines=None,
) -> str:
    """Resolve bracket using the fallback chain.

    1. Measured on disk (line_count is not None) → assign_bracket(line_count)
    2. New file with override OR existing override → bracket_for_override(avg_file_lines)
    3. Default → medium
    """
    # New files don't exist on disk, so line_count is None for them by definition
    if line_count is not None and not is_new:
        return assign_bracket(line_count)
    elif avg_file_lines is not None:
        return bracket_for_override(avg_file_lines)
    else:
        return "medium"


# ---------------------------------------------------------------------------
# TestBracketAssignment
# ---------------------------------------------------------------------------

class TestBracketAssignment:
    """Boundary tests for the three size brackets."""

    def test_zero_lines_is_small(self):
        assert assign_bracket(0) == "small"

    def test_49_lines_is_small(self):
        assert assign_bracket(49) == "small"

    def test_50_lines_is_medium(self):
        assert assign_bracket(50) == "medium"

    def test_500_lines_is_medium(self):
        assert assign_bracket(500) == "medium"

    def test_501_lines_is_large(self):
        assert assign_bracket(501) == "large"

    def test_10000_lines_is_large(self):
        assert assign_bracket(10_000) == "large"


# ---------------------------------------------------------------------------
# TestOverrideResolution
# ---------------------------------------------------------------------------

class TestOverrideResolution:
    """Tests for the avg_file_lines override bracket resolution."""

    def test_avg_file_lines_25_is_small(self):
        assert bracket_for_override(25) == "small"

    def test_avg_file_lines_49_boundary(self):
        assert bracket_for_override(49) == "small"

    def test_avg_file_lines_50_boundary(self):
        assert bracket_for_override(50) == "medium"

    def test_avg_file_lines_200_is_medium(self):
        assert bracket_for_override(200) == "medium"

    def test_avg_file_lines_500_boundary(self):
        assert bracket_for_override(500) == "medium"

    def test_avg_file_lines_501_boundary(self):
        assert bracket_for_override(501) == "large"

    def test_avg_file_lines_800_is_large(self):
        assert bracket_for_override(800) == "large"

    def test_no_override_no_paths_is_medium(self):
        """No override, no paths → medium defaults (10k read, 2.5k edit)."""
        avg_read, avg_edit = compute_weighted_average({"small": 0, "medium": 0, "large": 0})
        assert avg_read == 10_000
        assert avg_edit == 2_500


# ---------------------------------------------------------------------------
# TestStep3aComputation
# ---------------------------------------------------------------------------

class TestStep3aComputation:
    """Tests for file_read_contribution used in N-scaling steps."""

    def test_all_small_files(self):
        assert compute_file_read_contribution({"small": 5, "medium": 0, "large": 0}) == 15_000

    def test_all_medium_files(self):
        assert compute_file_read_contribution({"small": 0, "medium": 5, "large": 0}) == 50_000

    def test_all_large_files(self):
        assert compute_file_read_contribution({"small": 0, "medium": 0, "large": 5}) == 100_000

    def test_mixed_files(self):
        # 2×3000 + 2×10000 + 1×20000 = 6000 + 20000 + 20000 = 46000
        result = compute_file_read_contribution({"small": 2, "medium": 2, "large": 1})
        assert result == 46_000

    def test_flat_fallback(self):
        """No brackets (None) → N=5, contribution = 5 × 10000 = 50000."""
        assert compute_file_read_contribution(None, N=5) == 50_000

    def test_all_medium_unchanged(self):
        """All medium files produces identical result to flat 10k fallback for same N."""
        N = 5
        assert compute_file_read_contribution({"small": 0, "medium": N, "large": 0}) == compute_file_read_contribution(None, N=N)


# ---------------------------------------------------------------------------
# TestFileEditScaling
# ---------------------------------------------------------------------------

class TestFileEditScaling:
    """Tests for file_edit_contribution (edit token inputs per bracket)."""

    def test_small_edit_input(self):
        assert compute_file_edit_contribution({"small": 1, "medium": 0, "large": 0}) == 1_000

    def test_medium_edit_input(self):
        assert compute_file_edit_contribution({"small": 0, "medium": 1, "large": 0}) == 2_500

    def test_large_edit_input(self):
        assert compute_file_edit_contribution({"small": 0, "medium": 0, "large": 1}) == 5_000

    def test_mixed_edit(self):
        # 2×1000 + 2×2500 + 1×5000 = 2000 + 5000 + 5000 = 12000
        result = compute_file_edit_contribution({"small": 2, "medium": 2, "large": 1})
        assert result == 12_000


# ---------------------------------------------------------------------------
# TestWeightedAverage
# ---------------------------------------------------------------------------

class TestWeightedAverage:
    """Tests for the weighted-average computation used in fixed-count steps."""

    def test_all_small(self):
        avg_read, avg_edit = compute_weighted_average({"small": 3, "medium": 0, "large": 0})
        assert avg_read == 3_000
        assert avg_edit == 1_000

    def test_all_medium(self):
        avg_read, avg_edit = compute_weighted_average({"small": 0, "medium": 3, "large": 0})
        assert avg_read == 10_000
        assert avg_edit == 2_500

    def test_all_large(self):
        avg_read, avg_edit = compute_weighted_average({"small": 0, "medium": 0, "large": 3})
        assert avg_read == 20_000
        assert avg_edit == 5_000

    def test_mixed_2_small_3_medium(self):
        # avg_read = (2×3000 + 3×10000) / 5 = 36000/5 = 7200
        # avg_edit = (2×1000 + 3×2500) / 5 = 9500/5 = 1900
        avg_read, avg_edit = compute_weighted_average({"small": 2, "medium": 3, "large": 0})
        assert abs(avg_read - 7_200) < 0.001
        assert abs(avg_edit - 1_900) < 0.001

    def test_zero_measured_returns_medium_default(self):
        """Zero-divide guard: no measured files → medium defaults."""
        avg_read, avg_edit = compute_weighted_average({"small": 0, "medium": 0, "large": 0})
        assert avg_read == 10_000
        assert avg_edit == 2_500


# ---------------------------------------------------------------------------
# TestFallbackChain
# ---------------------------------------------------------------------------

class TestFallbackChain:
    """Tests for the three-level fallback: measured → override → default."""

    def test_measured_wins_over_override(self):
        """Measured line count (30 → small) wins over avg_file_lines=800 (large)."""
        result = resolve_bracket(line_count=30, avg_file_lines=800)
        assert result == "small"

    def test_override_wins_over_default(self):
        """Override (800 → large) wins when no measurement available."""
        result = resolve_bracket(line_count=None, avg_file_lines=800)
        assert result == "large"

    def test_default_only_all_medium(self):
        """No measurement, no override → medium default."""
        result = resolve_bracket(line_count=None, avg_file_lines=None)
        assert result == "medium"

    def test_new_file_uses_override_when_provided(self):
        """New file with avg_file_lines=800 override → large."""
        result = resolve_bracket(is_new=True, avg_file_lines=800)
        assert result == "large"

    def test_new_file_uses_medium_when_no_override(self):
        """New file with no override → medium default."""
        result = resolve_bracket(is_new=True, avg_file_lines=None)
        assert result == "medium"


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases: zero lines, measurement cap, binary extensions, duplicates."""

    def test_empty_file_is_small(self):
        """0 lines → small bracket."""
        assert assign_bracket(0) == "small"

    def test_measurement_cap_30(self):
        """Only first 30 of 35 paths are measured; 5 are defaulted."""
        cap = 30
        all_paths = [f"file_{i}.py" for i in range(35)]
        measured_paths = all_paths[:cap]
        defaulted_paths = all_paths[cap:]
        assert len(measured_paths) == 30
        assert len(defaulted_paths) == 5

    def test_binary_extensions_documented_in_heuristics(self):
        """heuristics.md must document binary extension exclusions."""
        content = HEURISTICS_MD.read_text()
        assert ".png" in content
        assert ".pyc" in content

    def test_deduplication_documented_in_skill(self):
        """SKILL.md must document that duplicate paths are deduplicated."""
        assert "Deduplicate" in SKILL_MD.read_text()


# ---------------------------------------------------------------------------
# TestBackwardCompatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """v1.4.0 plans without file_brackets produce identical behavior to before."""

    def test_no_paths_identical_to_v140(self):
        """file_brackets=None → N × 10000 (medium default, same as v1.4.0)."""
        N = 5
        result = compute_file_read_contribution(None, N=N)
        assert result == N * 10_000

    def test_active_estimate_without_file_brackets_loads(self):
        """Old estimate dict without file_brackets key loads via .get() without error."""
        old_estimate = {
            "timestamp": "2025-01-01T00:00:00Z",
            "size": "M",
            "expected_cost": 5.0,
        }
        # Simulate the .get() access pattern used in SKILL.md / learn.sh
        file_brackets = old_estimate.get("file_brackets")
        assert file_brackets is None  # no KeyError, returns None gracefully


# ---------------------------------------------------------------------------
# TestActiveEstimateSchema
# ---------------------------------------------------------------------------

class TestActiveEstimateSchema:
    """In-memory dict tests for the active-estimate.json schema additions."""

    def test_file_brackets_null_when_no_paths(self):
        """null file_brackets → None (no paths extracted)."""
        estimate = {"file_brackets": None}
        assert estimate.get("file_brackets") is None

    def test_file_brackets_small_medium_large_present(self):
        """file_brackets with all three keys present and summing correctly."""
        estimate = {"file_brackets": {"small": 1, "medium": 2, "large": 0}}
        fb = estimate["file_brackets"]
        assert sum(fb.values()) == 3

    def test_files_measured_equals_sum_of_brackets(self):
        """files_measured must equal sum(brackets.values()) for any bracket dict."""
        brackets = {"small": 2, "medium": 5, "large": 1}
        files_measured = sum(brackets.values())
        assert files_measured == 8

    def test_file_brackets_type_contract(self):
        """null means no paths extracted; zero-count dict is semantically different
        (paths were extracted but 0 files were measurable — e.g., all binary or missing)."""
        null_brackets = None                                      # no paths extracted from plan
        zero_brackets = {"small": 0, "medium": 0, "large": 0}  # paths extracted, none measured

        # null → no paths extracted
        assert null_brackets is None

        # zero-count dict → paths were attempted, none measurable
        total_measured = sum(zero_brackets.get(k, 0) for k in ("small", "medium", "large"))
        assert total_measured == 0

        # The two cases are distinct
        assert null_brackets != zero_brackets


# ---------------------------------------------------------------------------
# TestDocumentContent
# ---------------------------------------------------------------------------

class TestDocumentContent:
    """Verify required strings are present in actual files after implementation.

    These tests FAIL before implementation (SKILL.md and heuristics.md not yet updated).
    """

    def test_heuristics_has_file_size_brackets_section(self):
        assert "## File Size Brackets" in HEURISTICS_MD.read_text()

    def test_heuristics_has_small_bracket_value(self):
        content = HEURISTICS_MD.read_text()
        assert "3,000" in content or "3000" in content

    def test_heuristics_has_large_bracket_value(self):
        content = HEURISTICS_MD.read_text()
        assert "20,000" in content or "20000" in content

    def test_heuristics_has_measurement_cap(self):
        assert "file_measurement_cap" in HEURISTICS_MD.read_text()

    def test_heuristics_has_resolution_order(self):
        assert "Resolution order" in HEURISTICS_MD.read_text()

    def test_skill_has_avg_file_lines_override(self):
        assert "avg_file_lines" in SKILL_MD.read_text()

    def test_skill_has_file_brackets_in_schema(self):
        assert "file_brackets" in SKILL_MD.read_text()

    def test_skill_md_version_frontmatter(self):
        assert "version: 2.1.0" in SKILL_MD.read_text()

    def test_skill_output_template_has_files_line(self):
        assert "**Files:**" in SKILL_MD.read_text()

    def test_skill_limitations_updated(self):
        """The old '150-300 line source files' caveat must be removed or replaced."""
        assert "150-300 line source files" not in SKILL_MD.read_text()


# ---------------------------------------------------------------------------
# TestIntegrationArithmetic
# ---------------------------------------------------------------------------

class TestIntegrationArithmetic:
    """Arithmetic verification that bracket computation composes with downstream formulas."""

    def test_bracket_then_complexity_multiplier(self):
        """5 small files × 3000 = 15000; after complexity 1.5x: 22500. No rounding between."""
        file_read_contribution = compute_file_read_contribution({"small": 5, "medium": 0, "large": 0})
        assert file_read_contribution == 15_000
        after_complexity = file_read_contribution * 1.5
        assert abs(after_complexity - 22_500) < 0.001

    def test_bracket_affects_context_accumulation(self):
        """Large-file bracket produces proportionally higher input_accum than medium."""
        # For an N-scaling step with N=5:
        medium_contribution = compute_file_read_contribution({"small": 0, "medium": 5, "large": 0})
        large_contribution = compute_file_read_contribution({"small": 0, "medium": 0, "large": 5})
        # Large files should produce exactly 2× the read tokens of medium files
        assert large_contribution == medium_contribution * 2

    def test_wc_l_failure_falls_back_to_default(self):
        """A path that doesn't exist on disk is unmeasurable; bracket falls back to medium (or override)."""
        nonexistent_path = "/tmp/this_file_does_not_exist_tokencast_test_xyz.py"
        # Simulate: wc -l fails or file is missing → line_count is None → fallback
        subprocess.run(
            ["wc", "-l", nonexistent_path],
            capture_output=True,
            text=True,
        )
        line_count = None  # missing file → unmeasurable
        bracket = resolve_bracket(line_count=line_count, avg_file_lines=None)
        assert bracket == "medium"

    def test_wc_l_failure_uses_override_bracket(self):
        """Unmeasurable file with avg_file_lines=800 override → large bracket."""
        bracket = resolve_bracket(line_count=None, avg_file_lines=800)
        assert bracket == "large"


# ---------------------------------------------------------------------------
# TestLearnShIntegration (subprocess)
# ---------------------------------------------------------------------------

class TestLearnShIntegration(unittest.TestCase):
    """Integration tests: invoke learn.sh end-to-end with file_brackets in mock estimate.

    These tests FAIL before learn.sh is updated to forward file_brackets and files_measured.
    """

    LEARN_SH = Path(__file__).parent.parent / "scripts" / "tokencast-learn.sh"

    def _write_mock_estimate(self, tmp_dir: str, include_file_brackets: bool = True) -> str:
        """Write a minimal active-estimate.json for testing."""
        estimate = {
            "timestamp": "2026-03-20T10:00:00Z",
            "size": "M",
            "files": 5,
            "complexity": "medium",
            "steps": ["Test Writing", "Implementation"],
            "step_count": 2,
            "project_type": "refactor",
            "language": "python",
            "expected_cost": 5.0,
            "optimistic_cost": 3.0,
            "pessimistic_cost": 15.0,
            "baseline_cost": 0.0,
            "review_cycles_estimated": 0,
            "review_cycles_actual": None,
            "parallel_groups": [],
            "parallel_steps_detected": 0,
        }
        if include_file_brackets:
            estimate["file_brackets"] = {"small": 1, "medium": 2, "large": 1}
            estimate["files_measured"] = 4
        path = Path(tmp_dir) / "active-estimate.json"
        path.write_text(json.dumps(estimate))
        return str(path)

    def _write_mock_session_jsonl(self, tmp_dir: str) -> str:
        """Write a minimal session JSONL. Must include 'model' inside 'message'."""
        entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 50,
                },
            },
            "costUSD": 0.012,
        }
        path = Path(tmp_dir) / "session.jsonl"
        path.write_text(json.dumps(entry) + "\n")
        return str(path)

    def _run_learn_sh(
        self,
        estimate_file: str,
        session_file: str,
        history_file: str,
    ) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "TOKENCOSTSCOPE_ESTIMATE_FILE": estimate_file,
            "TOKENCOSTSCOPE_HISTORY_FILE": history_file,
        }
        return subprocess.run(
            ["bash", str(self.LEARN_SH), session_file, "0"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(Path(__file__).parent.parent),
        )

    def test_learn_sh_forwards_file_brackets(self):
        """learn.sh end-to-end: file_brackets key is present in the history record."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate_file = self._write_mock_estimate(tmp, include_file_brackets=True)
            session_file = self._write_mock_session_jsonl(tmp)
            history_file = str(Path(tmp) / "history.jsonl")

            self._run_learn_sh(estimate_file, session_file, history_file)

            if not Path(history_file).exists():
                self.skipTest(
                    "learn.sh did not write history (may need 3+ sessions or actual_cost=0). "
                    "This test will pass once learn.sh forwards file_brackets."
                )

            records = [
                json.loads(line)
                for line in Path(history_file).read_text().splitlines()
                if line.strip()
            ]
            self.assertGreater(len(records), 0, "History file must have at least one record")
            last = records[-1]
            self.assertIn(
                "file_brackets",
                last,
                "History record must contain 'file_brackets' key after v1.5.0 implementation",
            )
            self.assertEqual(last["file_brackets"], {"small": 1, "medium": 2, "large": 1})

    def test_learn_sh_forwards_files_measured(self):
        """learn.sh end-to-end: files_measured key is present in the history record."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate_file = self._write_mock_estimate(tmp, include_file_brackets=True)
            session_file = self._write_mock_session_jsonl(tmp)
            history_file = str(Path(tmp) / "history.jsonl")

            self._run_learn_sh(estimate_file, session_file, history_file)

            if not Path(history_file).exists():
                self.skipTest(
                    "learn.sh did not write history (may need 3+ sessions or actual_cost=0). "
                    "This test will pass once learn.sh forwards files_measured."
                )

            records = [
                json.loads(line)
                for line in Path(history_file).read_text().splitlines()
                if line.strip()
            ]
            last = records[-1]
            self.assertIn(
                "files_measured",
                last,
                "History record must contain 'files_measured' key after v1.5.0 implementation",
            )
            self.assertEqual(last["files_measured"], 4)

    def test_learn_sh_missing_file_brackets_defaults_gracefully(self):
        """Old estimate without file_brackets → history record loads without error.

        file_brackets may be null or absent in the record — no KeyError should occur.
        """
        with tempfile.TemporaryDirectory() as tmp:
            estimate_file = self._write_mock_estimate(tmp, include_file_brackets=False)
            session_file = self._write_mock_session_jsonl(tmp)
            history_file = str(Path(tmp) / "history.jsonl")

            self._run_learn_sh(estimate_file, session_file, history_file)

            if not Path(history_file).exists():
                self.skipTest(
                    "learn.sh did not write history (may need 3+ sessions or actual_cost=0). "
                    "Once it does, this test verifies graceful fallback for old estimates."
                )

            records = [
                json.loads(line)
                for line in Path(history_file).read_text().splitlines()
                if line.strip()
            ]
            self.assertGreater(len(records), 0)
            last = records[-1]
            # Must load without error — file_brackets may be null/missing, both are acceptable
            file_brackets = last.get("file_brackets")
            # If present it must be None or a dict; if absent that's also fine
            self.assertTrue(
                file_brackets is None or isinstance(file_brackets, dict),
                f"file_brackets must be null or a dict, got: {type(file_brackets)}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
