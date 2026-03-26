"""Tests for US-1c.02: report_step_cost MCP tool (absorbs US-1c.06).

Covers:
- compute_cost_from_usage() in pricing.py
- resolve_step_name() in step_names.py
- report_step_cost() in api.py
- handle_report_step_cost() in tokencast_mcp/tools/report_step_cost.py
"""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

# Ensure src/ is on path for all tests
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tokencast.pricing import (
    DEFAULT_MODEL,
    MODEL_PRICES,
    compute_cost_from_usage,
)
from tokencast.step_names import (
    DEFAULT_AGENT_TO_STEP,
    PR_REVIEW_LOOP_NAME,
    resolve_step_name,
)
from tokencast.api import report_step_cost

# MCP imports — only needed for TestReportStepCostMcpHandler.
# Imported lazily so Python 3.9 (no mcp package) can still run the other classes.
try:
    import mcp as _mcp  # noqa: F401 — presence check only
    from tokencast_mcp.config import ServerConfig
    from tokencast_mcp.tools.report_step_cost import (
        REPORT_STEP_COST_SCHEMA,
        handle_report_step_cost,
    )
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

_mcp_only = pytest.mark.skipif(
    not _MCP_AVAILABLE, reason="mcp not available in this Python environment"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_active_estimate(cal_dir: Path) -> None:
    """Create a minimal active-estimate.json so report_step_cost can proceed."""
    cal_dir.mkdir(parents=True, exist_ok=True)
    (cal_dir / "active-estimate.json").write_text('{"version": "0.1.0"}')


def _accumulator_hash(cal_dir: Path) -> str:
    active = cal_dir / "active-estimate.json"
    return hashlib.md5(str(active).encode()).hexdigest()[:12]


# _config_with_tmpdir is only used inside TestReportStepCostMcpHandler (MCP-only class).
# It is defined there as a method to avoid a module-level NameError on Python 3.9.


# ---------------------------------------------------------------------------
# TestComputeCostFromUsage
# ---------------------------------------------------------------------------


class TestComputeCostFromUsage:
    SONNET = "claude-sonnet-4-6"

    def test_all_four_token_types_sonnet(self):
        """Protocol Section 10 Example E worked example."""
        usage = {
            "tokens_in": 150000,
            "tokens_out": 25000,
            "tokens_cache_read": 80000,
            "tokens_cache_write": 20000,
        }
        result = compute_cost_from_usage(usage, self.SONNET)
        assert result == pytest.approx(0.924)

    def test_only_input_tokens(self):
        result = compute_cost_from_usage({"tokens_in": 1_000_000}, self.SONNET)
        assert result == pytest.approx(3.00)

    def test_only_output_tokens(self):
        result = compute_cost_from_usage({"tokens_out": 1_000_000}, self.SONNET)
        assert result == pytest.approx(15.00)

    def test_only_cache_read(self):
        result = compute_cost_from_usage({"tokens_cache_read": 1_000_000}, self.SONNET)
        assert result == pytest.approx(0.30)

    def test_only_cache_write(self):
        result = compute_cost_from_usage({"tokens_cache_write": 1_000_000}, self.SONNET)
        assert result == pytest.approx(3.75)

    def test_all_zeros_returns_zero(self):
        result = compute_cost_from_usage({}, self.SONNET)
        assert result == 0.0

    def test_opus_model_pricing(self):
        """Use MODEL_PRICES directly so the test stays correct if prices change."""
        opus = "claude-opus-4-6"
        expected = MODEL_PRICES[opus]["input"] * 1_000_000 / 1_000_000
        result = compute_cost_from_usage({"tokens_in": 1_000_000}, opus)
        assert result == pytest.approx(expected)

    def test_haiku_model_pricing(self):
        haiku = "claude-haiku-4-5"
        expected = MODEL_PRICES[haiku]["input"] * 1_000_000 / 1_000_000
        result = compute_cost_from_usage({"tokens_in": 1_000_000}, haiku)
        assert result == pytest.approx(expected)

    def test_partial_model_string_match(self):
        """'claude-sonnet' (no version suffix) should match claude-sonnet-4-6."""
        result_partial = compute_cost_from_usage({"tokens_in": 1_000_000}, "claude-sonnet")
        result_full = compute_cost_from_usage({"tokens_in": 1_000_000}, self.SONNET)
        assert result_partial == pytest.approx(result_full)

    def test_unknown_model_falls_back_to_default(self):
        """An unrecognized model string should use DEFAULT_MODEL (Sonnet) prices."""
        result_unknown = compute_cost_from_usage({"tokens_in": 1_000_000}, "gpt-4")
        result_sonnet = compute_cost_from_usage({"tokens_in": 1_000_000}, DEFAULT_MODEL)
        assert result_unknown == pytest.approx(result_sonnet)

    def test_default_model_arg(self):
        """Calling without model arg uses DEFAULT_MODEL prices."""
        result_default = compute_cost_from_usage({"tokens_in": 1_000_000})
        result_explicit = compute_cost_from_usage({"tokens_in": 1_000_000}, DEFAULT_MODEL)
        assert result_default == pytest.approx(result_explicit)

    def test_missing_fields_default_to_zero(self):
        """Only the fields present contribute to cost."""
        result = compute_cost_from_usage({"tokens_out": 1000}, self.SONNET)
        expected = 1000 * MODEL_PRICES[self.SONNET]["output"] / 1_000_000
        assert result == pytest.approx(expected)

    def test_protocol_field_names_not_jsonl_names(self):
        """Wrong field names (JSONL format) produce 0.0 — only protocol names work."""
        result = compute_cost_from_usage({"input_tokens": 1000}, self.SONNET)
        assert result == 0.0


# ---------------------------------------------------------------------------
# TestResolveStepName
# ---------------------------------------------------------------------------


class TestResolveStepName:
    def test_canonical_alias_lowercase(self):
        name, warning = resolve_step_name("researcher")
        assert name == "Research Agent"
        assert warning is None

    def test_canonical_alias_with_underscore(self):
        name, warning = resolve_step_name("staff_reviewer")
        assert name == "Staff Review"
        assert warning is None

    def test_canonical_alias_with_hyphen(self):
        name, warning = resolve_step_name("docs-updater")
        assert name == "Docs Updater"
        assert warning is None

    def test_canonical_name_direct(self):
        name, warning = resolve_step_name("Research Agent")
        assert name == "Research Agent"
        assert warning is None

    def test_unknown_name_accepted_as_is(self):
        name, warning = resolve_step_name("my-custom-step")
        assert name == "my-custom-step"
        assert warning is None

    def test_pr_review_loop_gets_warning(self):
        name, warning = resolve_step_name(PR_REVIEW_LOOP_NAME)
        assert name == PR_REVIEW_LOOP_NAME
        assert warning == "pr_review_loop_is_derived"

    def test_whitespace_stripped_for_lookup(self):
        name, warning = resolve_step_name("  researcher  ")
        assert name == "Research Agent"
        assert warning is None

    def test_agent_map_override_wins(self, tmp_path):
        agent_map = {"my-alias": "Custom Step"}
        (tmp_path / "agent-map.json").write_text(json.dumps(agent_map))
        name, warning = resolve_step_name("my-alias", tmp_path)
        assert name == "Custom Step"
        assert warning is None

    def test_agent_map_override_conflicts_with_default(self, tmp_path):
        """Config file wins for keys present in both maps."""
        agent_map = {"researcher": "MyResearch"}
        (tmp_path / "agent-map.json").write_text(json.dumps(agent_map))
        name, warning = resolve_step_name("researcher", tmp_path)
        assert name == "MyResearch"
        assert warning is None

    def test_missing_agent_map_file_handled_gracefully(self, tmp_path):
        nonexistent = tmp_path / "no-such-dir"
        name, warning = resolve_step_name("researcher", nonexistent)
        # Falls back to defaults — researcher is still resolvable
        assert name == "Research Agent"
        assert warning is None

    def test_malformed_agent_map_json_handled_gracefully(self, tmp_path):
        (tmp_path / "agent-map.json").write_text("NOT VALID JSON {{{")
        # Falls back to defaults silently
        name, warning = resolve_step_name("researcher", tmp_path)
        assert name == "Research Agent"
        assert warning is None

    def test_no_calibration_dir_uses_defaults_only(self):
        name, warning = resolve_step_name("implementer", calibration_dir=None)
        assert name == "Implementation"
        assert warning is None


# ---------------------------------------------------------------------------
# TestReportStepCostApi
# ---------------------------------------------------------------------------


class TestReportStepCostApi:
    """Tests for src/tokencast/api.py:report_step_cost()."""

    def _cal_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "calibration"

    def test_no_active_estimate_returns_error(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        cal.mkdir(parents=True, exist_ok=True)
        result = report_step_cost({"step_name": "Research Agent"}, calibration_dir=cal)
        assert result.get("error") == "no_active_estimate"

    def test_whitespace_only_step_name_returns_error(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost({"step_name": "   "}, calibration_dir=cal)
        assert result.get("error") == "invalid_step_name"

    def test_negative_cost_returns_error(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost(
            {"step_name": "Research Agent", "cost": -1.0}, calibration_dir=cal
        )
        assert result.get("error") == "invalid_cost"

    def test_negative_token_count_returns_error(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost(
            {"step_name": "Research Agent", "tokens_in": -100}, calibration_dir=cal
        )
        assert result.get("error") == "invalid_tokens"
        assert result.get("field") == "tokens_in"

    def test_basic_cost_call_returns_correct_shape(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost(
            {"step_name": "Research Agent", "cost": 1.20}, calibration_dir=cal
        )
        assert "error" not in result
        assert "attribution_protocol_version" in result
        assert "step_name" in result
        assert "cost_this_call" in result
        assert "cumulative_step_cost" in result
        assert "total_session_accumulated" in result

    def test_attribution_protocol_version_is_1(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost(
            {"step_name": "Research Agent", "cost": 0.5}, calibration_dir=cal
        )
        assert result["attribution_protocol_version"] == 1

    def test_cost_takes_precedence_over_tokens(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost(
            {
                "step_name": "Research Agent",
                "cost": 1.0,
                "tokens_in": 1_000_000,
            },
            calibration_dir=cal,
        )
        assert result["cost_this_call"] == pytest.approx(1.0)

    def test_token_cost_computed_correctly(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost(
            {
                "step_name": "Research Agent",
                "tokens_in": 1_000_000,
                "model": "claude-sonnet-4-6",
            },
            calibration_dir=cal,
        )
        assert result["cost_this_call"] == pytest.approx(3.0)

    def test_no_cost_no_tokens_records_zero_with_warning(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost({"step_name": "Research Agent"}, calibration_dir=cal)
        assert result["cost_this_call"] == 0.0
        assert "warning" in result

    def test_accumulation_same_step(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 1.0}, calibration_dir=cal)
        result2 = report_step_cost(
            {"step_name": "Research Agent", "cost": 1.0}, calibration_dir=cal
        )
        assert result2["cumulative_step_cost"] == pytest.approx(2.0)

    def test_accumulation_different_steps(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 1.0}, calibration_dir=cal)
        result2 = report_step_cost(
            {"step_name": "Implementation", "cost": 2.0}, calibration_dir=cal
        )
        assert result2["total_session_accumulated"] == pytest.approx(3.0)

    def test_accumulator_file_created_on_disk(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 1.0}, calibration_dir=cal)
        h = _accumulator_hash(cal)
        acc_file = cal / f"{h}-step-accumulator.json"
        assert acc_file.exists()

    def test_accumulator_file_schema(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 1.0}, calibration_dir=cal)
        h = _accumulator_hash(cal)
        acc_file = cal / f"{h}-step-accumulator.json"
        data = json.loads(acc_file.read_text())
        assert "attribution_protocol_version" in data
        assert "steps" in data
        assert "last_updated" in data
        assert isinstance(data["steps"], dict)

    def test_accumulator_file_persists_across_calls(self, tmp_path):
        """Simulates server restart: pre-populate the accumulator file,
        then verify a new call loads and adds to it."""
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        h = _accumulator_hash(cal)
        acc_file = cal / f"{h}-step-accumulator.json"
        # Write prior state manually (simulates a previous server session)
        prior = {
            "attribution_protocol_version": 1,
            "steps": {"Research Agent": 0.80},
            "last_updated": "2026-01-01T00:00:00Z",
        }
        acc_file.write_text(json.dumps(prior))
        # Now call report_step_cost — should load and add to the existing 0.80
        result = report_step_cost(
            {"step_name": "Research Agent", "cost": 0.40}, calibration_dir=cal
        )
        assert result["cumulative_step_cost"] == pytest.approx(1.20)

    def test_accumulator_atomic_rename_no_tmp_file_left(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        report_step_cost({"step_name": "Research Agent", "cost": 1.0}, calibration_dir=cal)
        tmp_files = list(cal.glob("*.tmp"))
        assert tmp_files == [], f"Unexpected .tmp files: {tmp_files}"

    def test_step_name_alias_resolved(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost(
            {"step_name": "researcher", "cost": 1.0}, calibration_dir=cal
        )
        assert result["step_name"] == "Research Agent"

    def test_pr_review_loop_warning(self, tmp_path):
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost(
            {"step_name": PR_REVIEW_LOOP_NAME, "cost": 1.0}, calibration_dir=cal
        )
        assert "warning" in result
        assert "pr_review_loop_is_derived" in result["warning"]

    def test_zero_cost_no_warning_when_cost_explicitly_zero(self, tmp_path):
        """Explicitly passing cost=0.0 is valid and must NOT produce a warning."""
        cal = self._cal_dir(tmp_path)
        _make_active_estimate(cal)
        result = report_step_cost(
            {"step_name": "Research Agent", "cost": 0.0}, calibration_dir=cal
        )
        assert result["cost_this_call"] == 0.0
        # The "no cost or token data provided" warning must NOT appear
        warning = result.get("warning", "")
        assert "No cost or token data" not in warning


# ---------------------------------------------------------------------------
# TestReportStepCostMcpHandler
# ---------------------------------------------------------------------------


@_mcp_only
class TestReportStepCostMcpHandler:
    """Tests for src/tokencast_mcp/tools/report_step_cost.py."""

    def _config(self, tmp_path: Path) -> "ServerConfig":  # type: ignore[name-defined]
        return ServerConfig.from_args(str(tmp_path / "calibration"), None)

    def test_schema_type_is_object(self):
        assert REPORT_STEP_COST_SCHEMA["type"] == "object"

    def test_schema_required_fields(self):
        assert "step_name" in REPORT_STEP_COST_SCHEMA["required"]

    def test_schema_step_name_property_exists(self):
        assert "step_name" in REPORT_STEP_COST_SCHEMA["properties"]

    def test_schema_cost_minimum_zero(self):
        assert REPORT_STEP_COST_SCHEMA["properties"]["cost"]["minimum"] == 0

    def test_schema_additional_properties_false(self):
        assert REPORT_STEP_COST_SCHEMA.get("additionalProperties") is False

    def test_handler_no_active_estimate_raises_value_error(self, tmp_path):
        config = self._config(tmp_path)
        config.ensure_dirs()
        with pytest.raises(ValueError):
            asyncio.run(
                handle_report_step_cost({"step_name": "Research Agent"}, config)
            )

    def test_handler_whitespace_step_name_raises_value_error(self, tmp_path):
        config = self._config(tmp_path)
        config.ensure_dirs()
        _make_active_estimate(config.calibration_dir)
        with pytest.raises(ValueError):
            asyncio.run(handle_report_step_cost({"step_name": "   "}, config))

    def test_handler_valid_call_returns_correct_shape(self, tmp_path):
        config = self._config(tmp_path)
        config.ensure_dirs()
        _make_active_estimate(config.calibration_dir)
        result = asyncio.run(
            handle_report_step_cost(
                {"step_name": "Research Agent", "cost": 1.0}, config
            )
        )
        assert result["attribution_protocol_version"] == 1
        assert "step_name" in result
        assert "cost_this_call" in result
        assert "cumulative_step_cost" in result
        assert "total_session_accumulated" in result

    def test_handler_passes_calibration_dir_from_config(self, tmp_path):
        """Verify accumulator file is created in config.calibration_dir."""
        config = self._config(tmp_path)
        config.ensure_dirs()
        _make_active_estimate(config.calibration_dir)
        asyncio.run(
            handle_report_step_cost(
                {"step_name": "Research Agent", "cost": 0.5}, config
            )
        )
        h = _accumulator_hash(config.calibration_dir)
        acc_file = config.calibration_dir / f"{h}-step-accumulator.json"
        assert acc_file.exists(), (
            f"Expected accumulator at {acc_file}, not found. "
            f"Dir contents: {list(config.calibration_dir.iterdir())}"
        )
