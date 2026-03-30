"""Tests for the first-run welcome note in handle_estimate_cost()."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest.importorskip("mcp")

from tokencast_mcp.tools.estimate_cost import handle_estimate_cost, _format_markdown_table


def _make_config(tmp_path, with_history=False, empty_history=False):
    """Return a mock ServerConfig pointing to tmp_path as calibration_dir."""
    config = MagicMock()
    config.calibration_dir = tmp_path
    config.project_dir = None
    if with_history and not empty_history:
        history = tmp_path / "history.jsonl"
        history.write_text(json.dumps({"actual_cost": 1.0, "expected_cost": 1.0}) + "\n")
    elif empty_history:
        history = tmp_path / "history.jsonl"
        history.write_text("")
    return config


_MINIMAL_API_RESULT = {
    "version": "0.1.2",
    "estimate": {"optimistic": 0.10, "expected": 0.20, "pessimistic": 0.40},
    "steps": [],
    "metadata": {
        "size": "S",
        "files": 3,
        "complexity": "low",
        "project_type": "unknown",
        "language": "unknown",
        "review_cycles": 1,
        "file_brackets": None,
        "files_measured": 0,
        "parallel_groups": [],
        "parallel_steps_detected": 0,
        "pricing_last_updated": "2025-01-01",
        "pricing_stale": False,
        "pipeline_signature": "implementation",
    },
    "step_costs": {},
}

_MINIMAL_PARAMS = {
    "size": "S",
    "files": 3,
    "complexity": "low",
}


@pytest.mark.skipif(sys.version_info < (3, 10), reason="MCP requires Python 3.10+")
def test_first_run_note_present_when_history_empty_file(tmp_path):
    config = _make_config(tmp_path, empty_history=True)
    with patch("tokencast_mcp.tools.estimate_cost._api_estimate_cost", return_value=dict(_MINIMAL_API_RESULT)):
        result = asyncio.run(handle_estimate_cost(_MINIMAL_PARAMS, config))
    assert "First run:" in result["text"]
    assert "No calibration data yet" in result["text"]


@pytest.mark.skipif(sys.version_info < (3, 10), reason="MCP requires Python 3.10+")
def test_first_run_note_present_when_history_missing(tmp_path):
    config = _make_config(tmp_path, with_history=False)
    with patch("tokencast_mcp.tools.estimate_cost._api_estimate_cost", return_value=dict(_MINIMAL_API_RESULT)):
        result = asyncio.run(handle_estimate_cost(_MINIMAL_PARAMS, config))
    assert "First run:" in result["text"]
    assert "No calibration data yet" in result["text"]


@pytest.mark.skipif(sys.version_info < (3, 10), reason="MCP requires Python 3.10+")
def test_first_run_note_absent_when_history_has_data(tmp_path):
    config = _make_config(tmp_path, with_history=True)
    with patch("tokencast_mcp.tools.estimate_cost._api_estimate_cost", return_value=dict(_MINIMAL_API_RESULT)):
        result = asyncio.run(handle_estimate_cost(_MINIMAL_PARAMS, config))
    assert "First run:" not in result["text"]


@pytest.mark.skipif(sys.version_info < (3, 10), reason="MCP requires Python 3.10+")
def test_first_run_note_appears_after_cost_table(tmp_path):
    config = _make_config(tmp_path, with_history=False)
    with patch("tokencast_mcp.tools.estimate_cost._api_estimate_cost", return_value=dict(_MINIMAL_API_RESULT)):
        result = asyncio.run(handle_estimate_cost(_MINIMAL_PARAMS, config))
    text = result["text"]
    assert "First run:" in text
    first_run_idx = text.index("First run:")
    assert first_run_idx > 0


@pytest.mark.skipif(sys.version_info < (3, 10), reason="MCP requires Python 3.10+")
def test_first_run_note_not_in_format_markdown_table():
    result = _format_markdown_table(_MINIMAL_API_RESULT)
    assert "First run:" not in result
