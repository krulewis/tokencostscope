# Run with: /usr/bin/python3 -m pytest tests/test_estimate_cost_tool.py
# Do NOT use bare 'python3 -m pytest' -- Homebrew Python 3.14 does not have pytest.
"""Integration tests for the estimate_cost MCP tool handler (US-1b.04).

Covers:
- Output shape (version, estimate, steps, metadata, step_costs, text keys)
- active-estimate.json written with all required fields
- last-estimate.md written and parseable by parse_last_estimate.py
- Step accumulator is cleaned up on new estimate
- continuation: false in active-estimate.json
- Markdown table format
- Edge cases: zero files, size variants, complexity variants, parallel_groups, file_paths
"""

import asyncio
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

# Skip entire module if mcp is not available (e.g. Python 3.9 test runner)
mcp = pytest.importorskip("mcp")

from tokencast_mcp.config import ServerConfig  # noqa: E402
from tokencast_mcp.tools.estimate_cost import handle_estimate_cost  # noqa: E402

# ---------------------------------------------------------------------------
# Load parse_last_estimate module via importlib
# ---------------------------------------------------------------------------
_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
_parse_spec = importlib.util.spec_from_file_location(
    "parse_last_estimate",
    _scripts_dir / "parse_last_estimate.py",
)
assert _parse_spec is not None and _parse_spec.loader is not None
_parse_mod = importlib.util.module_from_spec(_parse_spec)
_parse_spec.loader.exec_module(_parse_mod)  # type: ignore[union-attr]
parse_last_estimate = _parse_mod.parse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_ACTIVE_ESTIMATE_FIELDS = {
    "timestamp",
    "size",
    "files",
    "complexity",
    "steps",
    "step_count",
    "project_type",
    "language",
    "expected_cost",
    "optimistic_cost",
    "pessimistic_cost",
    "baseline_cost",
    "review_cycles_estimated",
    "review_cycles_actual",
    "parallel_groups",
    "parallel_steps_detected",
    "file_brackets",
    "files_measured",
    "step_costs",
    "continuation",
}


def _make_config(tmp_path: Path) -> ServerConfig:
    return ServerConfig.from_args(str(tmp_path / "calibration"), None)


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _call(params: dict, tmp_path: Path) -> tuple[dict, ServerConfig]:
    config = _make_config(tmp_path)
    result = _run(handle_estimate_cost(params, config))
    return result, config


# ---------------------------------------------------------------------------
# TestEstimateCostOutputShape
# ---------------------------------------------------------------------------

class TestEstimateCostOutputShape:
    """Verify the shape of the dict returned by handle_estimate_cost."""

    def test_result_has_version_key(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        assert "version" in result
        assert isinstance(result["version"], str)
        assert result["version"]

    def test_result_has_estimate_key_with_three_bands(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        estimate = result["estimate"]
        assert "optimistic" in estimate
        assert "expected" in estimate
        assert "pessimistic" in estimate
        assert estimate["optimistic"] > 0
        assert estimate["expected"] > 0
        assert estimate["pessimistic"] > 0

    def test_result_has_steps_list(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        steps = result["steps"]
        assert isinstance(steps, list)
        assert len(steps) >= 1
        for step in steps:
            assert "name" in step
            assert "model" in step
            assert "cal" in step
            assert "optimistic" in step
            assert "expected" in step
            assert "pessimistic" in step

    def test_result_has_metadata_key(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        meta = result["metadata"]
        for key in (
            "size", "files", "complexity", "project_type", "language",
            "review_cycles", "file_brackets", "files_measured", "parallel_groups",
            "parallel_steps_detected", "pricing_last_updated", "pricing_stale",
            "pipeline_signature",
        ):
            assert key in meta, f"metadata missing key: {key!r}"

    def test_result_has_step_costs(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        assert "step_costs" in result
        assert isinstance(result["step_costs"], dict)
        for v in result["step_costs"].values():
            assert isinstance(v, float)

    def test_result_has_text_field(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        assert "text" in result
        assert isinstance(result["text"], str)
        assert result["text"]

    def test_text_contains_tokencast_header(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        assert result["text"].startswith("## tokencast estimate")

    def test_text_contains_total_row(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        assert "TOTAL" in result["text"]

    def test_text_contains_step_rows(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        for step in result["steps"]:
            assert step["name"] in result["text"], f"step {step['name']!r} not in text"

    def test_optimistic_less_than_expected_less_than_pessimistic(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        est = result["estimate"]
        assert est["optimistic"] <= est["expected"] <= est["pessimistic"]


# ---------------------------------------------------------------------------
# TestEstimateCostFileWrites
# ---------------------------------------------------------------------------

class TestEstimateCostFileWrites:
    """Verify that active-estimate.json and last-estimate.md are written correctly."""

    def test_active_estimate_json_is_written(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        assert config.active_estimate_path.exists()

    def test_active_estimate_json_is_valid_json(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        data = json.loads(config.active_estimate_path.read_text())
        assert isinstance(data, dict)

    def test_active_estimate_json_has_all_required_fields(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        data = json.loads(config.active_estimate_path.read_text())
        missing = _REQUIRED_ACTIVE_ESTIMATE_FIELDS - set(data.keys())
        assert not missing, f"active-estimate.json missing fields: {missing}"

    def test_active_estimate_json_continuation_is_false(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        data = json.loads(config.active_estimate_path.read_text())
        assert data["continuation"] is False

    def test_active_estimate_json_baseline_cost_is_zero(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        data = json.loads(config.active_estimate_path.read_text())
        assert data["baseline_cost"] == 0

    def test_active_estimate_json_review_cycles_actual_is_null(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        data = json.loads(config.active_estimate_path.read_text())
        assert data["review_cycles_actual"] is None

    def test_active_estimate_json_step_costs_is_dict_of_floats(self, tmp_path):
        result, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        data = json.loads(config.active_estimate_path.read_text())
        assert isinstance(data["step_costs"], dict)
        for v in data["step_costs"].values():
            assert isinstance(v, (int, float))

    def test_active_estimate_json_values_match_result(self, tmp_path):
        result, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        data = json.loads(config.active_estimate_path.read_text())
        assert data["expected_cost"] == pytest.approx(result["estimate"]["expected"])
        assert data["size"] == result["metadata"]["size"]
        assert data["files"] == result["metadata"]["files"]
        assert data["complexity"] == result["metadata"]["complexity"]

    def test_last_estimate_md_is_written(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        assert config.last_estimate_path.exists()

    def test_last_estimate_md_contains_optimistic_band(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        text = config.last_estimate_path.read_text()
        assert "Optimistic" in text

    def test_last_estimate_md_contains_expected_band(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        text = config.last_estimate_path.read_text()
        assert "Expected" in text

    def test_last_estimate_md_contains_baseline_cost_footer(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        text = config.last_estimate_path.read_text()
        assert "Baseline Cost: $0" in text

    def test_last_estimate_md_parseable_by_parse_last_estimate(self, tmp_path):
        _, config = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        content = config.last_estimate_path.read_text()
        parsed = parse_last_estimate(content, mtime=None)
        assert parsed is not None
        assert "expected_cost" in parsed

    def test_write_errors_do_not_raise(self, tmp_path):
        """Handler must return a result even if the calibration dir is read-only."""
        config = _make_config(tmp_path)
        # Create calibration dir first
        config.calibration_dir.mkdir(parents=True, exist_ok=True)
        # Make it read-only
        os.chmod(str(config.calibration_dir), 0o555)
        try:
            result = _run(handle_estimate_cost(
                {"size": "M", "files": 5, "complexity": "medium"}, config
            ))
            assert "estimate" in result
        finally:
            # Restore so tmp_path cleanup works
            os.chmod(str(config.calibration_dir), 0o755)


# ---------------------------------------------------------------------------
# TestEstimateCostAccumulatorCleanup
# ---------------------------------------------------------------------------

class TestEstimateCostAccumulatorCleanup:
    """Verify that stale step-accumulator files are cleaned up on new estimate."""

    def _accumulator_path(self, config: ServerConfig) -> Path:
        """Compute the expected accumulator path for a given config."""
        hash_prefix = hashlib.md5(
            str(config.active_estimate_path).encode()
        ).hexdigest()[:12]
        return config.calibration_dir / f"{hash_prefix}-step-accumulator.json"

    def test_stale_accumulator_is_deleted_on_new_estimate(self, tmp_path):
        config = _make_config(tmp_path)
        # Create calibration dir and pre-create a stale accumulator file
        config.calibration_dir.mkdir(parents=True, exist_ok=True)
        acc_path = self._accumulator_path(config)
        acc_path.write_text('{"Research Agent": 1.23}\n')
        assert acc_path.exists()

        _run(handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config))

        assert not acc_path.exists(), "Stale accumulator should be deleted after new estimate"

    def test_accumulator_cleanup_only_targets_correct_hash(self, tmp_path):
        config = _make_config(tmp_path)
        config.calibration_dir.mkdir(parents=True, exist_ok=True)

        # The matching accumulator (should be deleted)
        acc_path = self._accumulator_path(config)
        acc_path.write_text('{"Research Agent": 1.23}\n')

        # An unrelated accumulator with a different hash (should be left alone)
        other_acc = config.calibration_dir / "aabbccddee11-step-accumulator.json"
        other_acc.write_text('{"other": 0.5}\n')

        _run(handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config))

        assert not acc_path.exists(), "Matching accumulator should be deleted"
        assert other_acc.exists(), "Non-matching accumulator should not be touched"

    def test_no_accumulator_file_does_not_error(self, tmp_path):
        config = _make_config(tmp_path)
        # No accumulator file pre-created — should not raise
        result = _run(
            handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config)
        )
        assert "estimate" in result


# ---------------------------------------------------------------------------
# TestEstimateCostEdgeCases
# ---------------------------------------------------------------------------

class TestEstimateCostEdgeCases:
    """Edge cases and parameter variants."""

    def test_zero_files_returns_valid_estimate(self, tmp_path):
        result, _ = _call({"size": "XS", "files": 0, "complexity": "low"}, tmp_path)
        est = result["estimate"]
        assert est["optimistic"] >= 0
        assert est["expected"] >= 0
        assert est["pessimistic"] >= 0

    def test_l_size_costs_more_than_xs(self, tmp_path):
        result_l, _ = _call({"size": "L", "files": 5, "complexity": "medium"}, tmp_path)
        result_xs, _ = _call({"size": "XS", "files": 5, "complexity": "medium"}, tmp_path)
        assert result_l["estimate"]["expected"] > result_xs["estimate"]["expected"]

    def test_review_cycles_zero_excludes_pr_review_loop(self, tmp_path):
        result, _ = _call(
            {"size": "M", "files": 5, "complexity": "medium", "review_cycles": 0},
            tmp_path,
        )
        step_names = [s["name"] for s in result["steps"]]
        assert "PR Review Loop" not in step_names

    def test_review_cycles_nonzero_includes_pr_review_loop(self, tmp_path):
        result, _ = _call(
            {
                "size": "M",
                "files": 5,
                "complexity": "medium",
                "review_cycles": 2,
                "steps": [
                    "Research Agent",
                    "PM Agent",
                    "Architect Agent",
                    "Engineer Initial Plan",
                    "Staff Engineer Review",
                    "Engineer Final Plan",
                    "Implementation",
                ],
            },
            tmp_path,
        )
        step_names = [s["name"] for s in result["steps"]]
        assert "PR Review Loop" in step_names

    def test_avg_file_lines_override_uses_bracket(self, tmp_path):
        result, _ = _call(
            {"size": "M", "files": 5, "complexity": "medium", "avg_file_lines": 100},
            tmp_path,
        )
        # avg_file_lines=100 falls in the medium bracket (50-500).
        # The engine populates file_brackets from the override even without file_paths,
        # so file_brackets is a dict (not None). All 5 files go into medium.
        fb = result["metadata"]["file_brackets"]
        assert fb is not None
        assert fb["medium"] == 5
        assert fb["small"] == 0
        assert fb["large"] == 0

    def test_parallel_groups_accepted(self, tmp_path):
        result, _ = _call(
            {
                "size": "M",
                "files": 5,
                "complexity": "medium",
                "parallel_groups": [["Research Agent", "PM Agent"]],
            },
            tmp_path,
        )
        assert "estimate" in result
        assert result["metadata"]["parallel_steps_detected"] > 0

    def test_unknown_steps_in_override_are_skipped(self, tmp_path):
        result, _ = _call(
            {
                "size": "M",
                "files": 5,
                "complexity": "medium",
                "steps": ["Research Agent", "Unknown Step XYZ"],
            },
            tmp_path,
        )
        step_names = [s["name"] for s in result["steps"]]
        assert "Unknown Step XYZ" not in step_names

    def test_high_complexity_costs_more_than_low(self, tmp_path):
        result_high, _ = _call({"size": "M", "files": 5, "complexity": "high"}, tmp_path)
        result_low, _ = _call({"size": "M", "files": 5, "complexity": "low"}, tmp_path)
        assert result_high["estimate"]["expected"] > result_low["estimate"]["expected"]

    def test_calibration_dir_absent_uses_no_calibration(self, tmp_path):
        nonexistent = tmp_path / "does_not_exist" / "calibration"
        config = ServerConfig(calibration_dir=nonexistent, project_dir=None)
        result = _run(
            handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config)
        )
        assert "estimate" in result
        est = result["estimate"]
        assert est["expected"] > 0

    def test_pricing_stale_flag_in_metadata(self, tmp_path):
        """pricing_stale should be True when LAST_UPDATED is >90 days ago."""
        import tokencast.pricing as pricing_module
        with patch.object(pricing_module, "LAST_UPDATED", "2000-01-01"):
            result, _ = _call(
                {"size": "M", "files": 5, "complexity": "medium"}, tmp_path
            )
        assert result["metadata"]["pricing_stale"] is True


# ---------------------------------------------------------------------------
# TestEstimateCostWithFilePaths
# ---------------------------------------------------------------------------

class TestEstimateCostWithFilePaths:
    """Tests for file_paths parameter and file measurement integration."""

    def test_file_paths_with_existing_files_measured(self, tmp_path):
        # Create 3 files with known line counts
        f1 = tmp_path / "small.py"
        f1.write_text("\n" * 20)        # 20 lines → small bracket (≤49)
        f2 = tmp_path / "medium.py"
        f2.write_text("\n" * 100)       # 100 lines → medium bracket (50-500)
        f3 = tmp_path / "large.py"
        f3.write_text("\n" * 600)       # 600 lines → large bracket (≥501)

        config = ServerConfig.from_args(
            str(tmp_path / "calibration"), str(tmp_path)
        )
        result = _run(
            handle_estimate_cost(
                {
                    "size": "M",
                    "files": 3,
                    "complexity": "medium",
                    "file_paths": ["small.py", "medium.py", "large.py"],
                },
                config,
            )
        )
        assert result["metadata"]["files_measured"] == 3
        fb = result["metadata"]["file_brackets"]
        assert fb is not None
        assert fb["small"] == 1
        assert fb["medium"] == 1
        assert fb["large"] == 1

    def test_file_paths_with_missing_files_uses_defaults(self, tmp_path):
        result, _ = _call(
            {
                "size": "M",
                "files": 3,
                "complexity": "medium",
                "file_paths": ["does_not_exist_1.py", "does_not_exist_2.py"],
            },
            tmp_path,
        )
        assert "estimate" in result
        # files_measured may be 0 for non-existent files
        assert result["metadata"]["files_measured"] >= 0

    def test_file_paths_empty_list_uses_medium_default(self, tmp_path):
        result, _ = _call(
            {"size": "M", "files": 3, "complexity": "medium", "file_paths": []},
            tmp_path,
        )
        # Empty file_paths list → no measurement path taken → file_brackets is None
        assert result["metadata"]["file_brackets"] is None

    def test_file_paths_resolution_relative_to_project_dir(self, tmp_path):
        proj = tmp_path / "myproject"
        proj.mkdir()
        f = proj / "myfile.py"
        f.write_text("\n" * 75)  # medium

        config = ServerConfig.from_args(
            str(tmp_path / "calibration"), str(proj)
        )
        result = _run(
            handle_estimate_cost(
                {
                    "size": "S",
                    "files": 1,
                    "complexity": "low",
                    "file_paths": ["myfile.py"],
                },
                config,
            )
        )
        assert result["metadata"]["files_measured"] == 1
        fb = result["metadata"]["file_brackets"]
        assert fb is not None
        assert fb["medium"] == 1


# ---------------------------------------------------------------------------
# TestMaxPlanQuotaOutput
# ---------------------------------------------------------------------------

class TestMaxPlanQuotaOutput:
    """Tests for Claude Max plan quota-percentage line in estimate output."""

    def _make_config_with_max_plan(self, tmp_path: Path, max_plan: str) -> ServerConfig:
        config = ServerConfig.from_args(str(tmp_path / "calibration"), None, max_plan=max_plan)
        return config

    def test_quota_line_absent_when_max_plan_not_set(self, tmp_path):
        result, _ = _call({"size": "M", "files": 5, "complexity": "medium"}, tmp_path)
        assert "session window" not in result["text"].lower()
        assert "Max plan:" not in result["text"]

    def test_quota_line_absent_when_max_plan_is_none(self, tmp_path):
        config = ServerConfig.from_args(str(tmp_path / "calibration"), None, max_plan=None)
        result = _run(handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config))
        assert "session window" not in result["text"].lower()

    def test_quota_line_present_for_5x_plan(self, tmp_path):
        config = self._make_config_with_max_plan(tmp_path, "5x")
        result = _run(handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config))
        assert "session window" in result["text"].lower()

    def test_quota_line_present_for_20x_plan(self, tmp_path):
        config = self._make_config_with_max_plan(tmp_path, "20x")
        result = _run(handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config))
        assert "session window" in result["text"].lower()

    def test_quota_line_contains_percentage_symbol(self, tmp_path):
        config = self._make_config_with_max_plan(tmp_path, "5x")
        result = _run(handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config))
        assert "%" in result["text"]

    def test_quota_line_mentions_plan_tier(self, tmp_path):
        config = self._make_config_with_max_plan(tmp_path, "5x")
        result = _run(handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config))
        assert "5x" in result["text"]

    def test_env_var_fallback_sets_max_plan(self, tmp_path):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"TOKENCAST_MAX_PLAN": "5x"}):
            config = ServerConfig.from_args(str(tmp_path / "calibration"), None, max_plan=None)
            assert config.max_plan == "5x"

    def test_invalid_env_var_ignored(self, tmp_path):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"TOKENCAST_MAX_PLAN": "pro"}):
            config = ServerConfig.from_args(str(tmp_path / "calibration"), None, max_plan=None)
            assert config.max_plan is None

    def test_quota_20x_shows_lower_percentage_than_5x(self, tmp_path):
        config_5x = self._make_config_with_max_plan(tmp_path, "5x")
        config_20x = self._make_config_with_max_plan(tmp_path, "20x")
        r5 = _run(handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config_5x))
        r20 = _run(handle_estimate_cost({"size": "M", "files": 5, "complexity": "medium"}, config_20x))
        # Both have a quota line — extract the percentage from each and compare
        import re
        def extract_pct(text):
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
            return float(m.group(1)) if m else None
        pct5 = extract_pct(r5["text"])
        pct20 = extract_pct(r20["text"])
        assert pct5 is not None and pct20 is not None
        assert pct5 > pct20
